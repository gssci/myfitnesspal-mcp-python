"""Register MCP tools with flat, self-describing argument schemas.

Every tool in this package is written as ``async def tool(params: SomeInput)``.
That is pleasant Python, but it makes FastMCP publish a schema whose only
property is a reference to a definition::

    {"properties": {"params": {"$ref": "#/$defs/GetMealFoodsInput"}},
     "$defs": {"GetMealFoodsInput": {"properties": {"meal": ...}}}}

Some clients cannot express that. The Ollama Python client validates each tool
through ``ollama._types.Tool``, whose per-property model only carries ``type``,
``items``, ``description`` and ``enum``; ``$ref`` is not a field, so pydantic
drops it and ``model_dump(exclude_none=True)`` sends ``"params": {}``. The
model is then asked for a required argument it has never been shown, and it
invents field names (``meal_number`` instead of ``meal``) or omits required
ones entirely. llama-server, which receives the schema verbatim, resolves the
reference and behaves correctly — the same model looks far dumber on Ollama
purely because of this.

``flat_tool`` fixes that at the source: the tool is registered under a
generated wrapper whose signature is the input model's fields, so the published
schema is one flat object of scalar properties with no ``$ref`` and no
``$defs``. Enums are inlined as ``Literal`` for the same reason. The wrapper
rebuilds the original model and calls the original function, so tool bodies and
their ``params.x`` access are untouched, and the model keeps validating exactly
as before.
"""

from __future__ import annotations

import inspect
import types
from collections.abc import Callable
from enum import Enum
from typing import Annotated, Any, Literal, Union, get_args, get_origin, get_type_hints

from pydantic import BaseModel, BeforeValidator, Field

from .app import mcp

# `str | None` produces types.UnionType, `Optional[str]` produces typing.Union;
# both appear in the input models.
_UNION_ORIGINS = (Union, types.UnionType)

# Keys an Ollama tool property can carry; see the module docstring. Kept here so
# tests can assert the published schema survives that round trip.
OLLAMA_PROPERTY_KEYS = frozenset({"type", "items", "description", "enum"})

# Arguments every tool declares but no model should be shown. `response_format`
# exists for Python callers and tests; advertising it cost ~355 tokens of schema
# across the tool surface, on every model call, for a knob the agent never turns.
HIDDEN_ARGUMENTS = frozenset({"response_format"})


def _strip_titles(node: Any) -> Any:
    """Drop pydantic's generated ``title`` keys from a published JSON schema.

    Pydantic labels every property ("Mfp Id", "Meal") and every argument model.
    Models ignore the field entirely, so it is pure prompt weight.
    """
    if isinstance(node, dict):
        return {key: _strip_titles(value) for key, value in node.items() if key != "title"}
    if isinstance(node, list):
        return [_strip_titles(value) for value in node]
    return node


def published_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Trim a tool's advertised schema to what a model has to read.

    Only the advertised copy is trimmed. Validation runs off the argument model
    FastMCP built from the signature, so a hidden argument stays accepted with
    its default if some caller does send it.
    """
    pruned = _strip_titles(schema)
    properties = pruned.get("properties")
    if isinstance(properties, dict):
        for name in HIDDEN_ARGUMENTS:
            properties.pop(name, None)
    required = pruned.get("required")
    if isinstance(required, list):
        pruned["required"] = [name for name in required if name not in HIDDEN_ARGUMENTS]
    return pruned


def _coerce_scalar_to_str(value: Any) -> Any:
    """Accept the numeric IDs that language models keep sending for string fields.

    The input models set ``coerce_numbers_to_str=True``; that config lives on
    the model, not on the argument model FastMCP generates from a signature, so
    flattening would otherwise make ``mfp_id=58995187319997`` a validation
    error. Strings are stripped to match ``str_strip_whitespace=True``.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    if isinstance(value, str):
        return value.strip()
    return value


def _inline_annotation(annotation: Any) -> Any:
    """Return an equivalent annotation that produces no ``$ref`` in JSON schema."""
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return Literal[tuple(member.value for member in annotation)]  # type: ignore[return-value]
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        raise TypeError(
            f"{annotation.__name__} is a nested model; flat tool schemas only "
            "support scalar fields. Flatten the model or register this tool "
            "with mcp.tool directly."
        )
    if get_origin(annotation) in _UNION_ORIGINS:
        return Union[tuple(_inline_annotation(arg) for arg in get_args(annotation))]
    return annotation


def _accepts_str(annotation: Any) -> bool:
    if annotation is str:
        return True
    if get_origin(annotation) in _UNION_ORIGINS:
        return any(arg is str for arg in get_args(annotation))
    return False


def _flat_parameters(model: type[BaseModel]) -> list[inspect.Parameter]:
    """Turn a model's fields into keyword-only parameters for a wrapper function."""
    parameters: list[inspect.Parameter] = []
    for name, field in model.model_fields.items():
        annotation = _inline_annotation(field.annotation)
        # field.metadata carries the constraints (Ge, Le, MinLen, pattern, and
        # any BeforeValidator declared on the model) in declaration order, so
        # copying it verbatim keeps the flat schema and the model in agreement.
        metadata: list[Any] = list(field.metadata)
        if _accepts_str(annotation):
            # Appended last, so it wraps the constraints and runs before them.
            metadata.append(BeforeValidator(_coerce_scalar_to_str))
        if field.description is not None:
            metadata.append(Field(description=field.description))
        # Annotated needs at least one metadata entry; a bare field keeps its type.
        annotated = Annotated[tuple([annotation, *metadata])] if metadata else annotation

        if field.is_required():
            default: Any = inspect.Parameter.empty
        else:
            default = field.get_default(call_default_factory=True)
            if isinstance(default, Enum):
                default = default.value

        parameters.append(
            inspect.Parameter(
                name,
                inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=annotated,
            )
        )
    return parameters


def _params_model(fn: Callable[..., Any]) -> type[BaseModel]:
    signature = inspect.signature(fn)
    names = [name for name in signature.parameters if name != "self"]
    if names != ["params"]:
        raise TypeError(
            f"{fn.__name__} must take exactly one 'params' argument to be "
            f"registered with flat_tool; got {names}."
        )
    model = get_type_hints(fn).get("params")
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise TypeError(f"{fn.__name__}'s 'params' argument must be annotated with a BaseModel.")
    return model


def _build_wrapper(fn: Callable[..., Any], model: type[BaseModel], name: str) -> Callable[..., Any]:
    parameters = _flat_parameters(model)
    return_annotation = get_type_hints(fn).get("return", inspect.Signature.empty)

    if inspect.iscoroutinefunction(fn):

        async def wrapper(**kwargs: Any) -> Any:
            return await fn(model(**kwargs))

    else:

        def wrapper(**kwargs: Any) -> Any:  # type: ignore[misc]
            return fn(model(**kwargs))

    wrapper.__name__ = name
    wrapper.__qualname__ = name
    wrapper.__doc__ = fn.__doc__
    wrapper.__module__ = fn.__module__
    wrapper.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        parameters, return_annotation=return_annotation
    )
    wrapper.__annotations__ = {param.name: param.annotation for param in parameters}
    if return_annotation is not inspect.Signature.empty:
        wrapper.__annotations__["return"] = return_annotation
    return wrapper


def flat_tool(**tool_kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a ``params``-model tool under a flattened signature.

    Takes the same keyword arguments as ``mcp.tool`` and returns the original
    function unchanged, so Python callers and tests keep passing a model
    instance while MCP clients see one flat object of scalars.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        model = _params_model(fn)
        name = tool_kwargs.get("name") or fn.__name__
        mcp.tool(**tool_kwargs)(_build_wrapper(fn, model, name))
        return fn

    return decorator
