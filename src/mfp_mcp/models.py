"""Validated input models for MCP tools."""

from pydantic import BaseModel, ConfigDict, Field

from .formatting import ResponseFormat


class GetDiaryInput(BaseModel):
    """Input model for getting food diary."""

    model_config = ConfigDict(str_strip_whitespace=True)

    date: str | None = Field(
        default=None,
        description="Date in YYYY-MM-DD format. Defaults to today if not specified.",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' for human-readable or 'json' for structured data",
    )


class SearchFoodInput(BaseModel):
    """Input model for searching foods."""

    model_config = ConfigDict(str_strip_whitespace=True)

    query: str = Field(
        ...,
        description="Search query for food items (e.g., 'chicken breast', 'apple')",
        min_length=1,
        max_length=200,
    )
    limit: int = Field(
        default=10,
        description="Maximum number of results to return",
        ge=1,
        le=50,
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' for human-readable or 'json' for structured data",
    )


class GetMealFoodsInput(BaseModel):
    """Input model for meal-specific recent and frequent foods."""

    model_config = ConfigDict(str_strip_whitespace=True)

    meal: int = Field(
        ...,
        description="Meal number: 0=Breakfast, 1=Lunch, 2=Dinner, 3=Snacks.",
        ge=0,
        le=3,
    )
    limit_per_list: int = Field(
        default=25,
        description="Maximum recent foods and frequent foods to return separately.",
        ge=1,
        le=50,
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' for human-readable or 'json' for structured data",
    )


class ResolveMealFoodInput(BaseModel):
    """Resolve a meal-history ID to an add-ready modern food ID."""

    model_config = ConfigDict(str_strip_whitespace=True)

    history_id: str = Field(
        ...,
        description="History ID returned by mfp_get_meal_foods.",
        min_length=1,
    )
    meal: int = Field(
        ...,
        description="Meal number used for the history lookup: 0=Breakfast, 1=Lunch, 2=Dinner, 3=Snacks.",
        ge=0,
        le=3,
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' for human-readable or 'json' for structured data",
    )


class GetFoodDetailsInput(BaseModel):
    """Input model for getting food item details."""

    model_config = ConfigDict(str_strip_whitespace=True)

    mfp_id: str = Field(
        ...,
        description="MyFitnessPal food item ID (obtained from search results)",
        min_length=1,
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' for human-readable or 'json' for structured data",
    )


class GetMeasurementsInput(BaseModel):
    """Input model for getting measurements."""

    model_config = ConfigDict(str_strip_whitespace=True)

    measurement: str = Field(
        default="Weight",
        description="Type of measurement to retrieve (e.g., 'Weight', 'Body Fat', 'Waist')",
    )
    start_date: str | None = Field(
        default=None,
        description="Start date in YYYY-MM-DD format. Defaults to 30 days ago.",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
    end_date: str | None = Field(
        default=None,
        description="End date in YYYY-MM-DD format. Defaults to today.",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' for human-readable or 'json' for structured data",
    )


class SetMeasurementInput(BaseModel):
    """Input model for setting a measurement."""

    model_config = ConfigDict(str_strip_whitespace=True)

    measurement: str = Field(
        default="Weight",
        description="Type of measurement to set (e.g., 'Weight', 'Body Fat', 'Waist')",
    )
    value: float = Field(
        ...,
        description="Measurement value (e.g., 185.5 for weight in lbs)",
        gt=0,
    )


class GetExercisesInput(BaseModel):
    """Input model for getting exercises."""

    model_config = ConfigDict(str_strip_whitespace=True)

    date: str | None = Field(
        default=None,
        description="Date in YYYY-MM-DD format. Defaults to today if not specified.",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' for human-readable or 'json' for structured data",
    )


class GetGoalsInput(BaseModel):
    """Input model for getting nutrition goals."""

    model_config = ConfigDict(str_strip_whitespace=True)

    date: str | None = Field(
        default=None,
        description="Date in YYYY-MM-DD format. Defaults to today if not specified.",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' for human-readable or 'json' for structured data",
    )


class SetGoalsInput(BaseModel):
    """Input model for setting nutrition goals."""

    model_config = ConfigDict(str_strip_whitespace=True)

    calories: int | None = Field(
        default=None,
        description="Daily calorie goal (e.g., 2000)",
        ge=500,
        le=10000,
    )
    protein: int | None = Field(
        default=None,
        description="Daily protein goal in grams (e.g., 150)",
        ge=0,
        le=1000,
    )
    carbohydrates: int | None = Field(
        default=None,
        description="Daily carbohydrate goal in grams (e.g., 200)",
        ge=0,
        le=2000,
    )
    fat: int | None = Field(
        default=None,
        description="Daily fat goal in grams (e.g., 65)",
        ge=0,
        le=500,
    )


class GetWaterInput(BaseModel):
    """Input model for getting water intake."""

    model_config = ConfigDict(str_strip_whitespace=True)

    date: str | None = Field(
        default=None,
        description="Date in YYYY-MM-DD format. Defaults to today if not specified.",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )


class GetReportInput(BaseModel):
    """Input model for getting nutrition reports."""

    model_config = ConfigDict(str_strip_whitespace=True)

    report_name: str = Field(
        default="Net Calories",
        description="Report name (e.g., 'Net Calories', 'Total Calories', 'Protein', 'Fat', 'Carbs')",
    )
    start_date: str | None = Field(
        default=None,
        description="Start date in YYYY-MM-DD format. Defaults to 7 days ago.",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
    end_date: str | None = Field(
        default=None,
        description="End date in YYYY-MM-DD format. Defaults to today.",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' for human-readable or 'json' for structured data",
    )


class AddFoodToDiaryInput(BaseModel):
    """Add a measured amount of food to the diary."""

    model_config = ConfigDict(str_strip_whitespace=True)

    mfp_id: str = Field(
        ...,
        description=(
            "Modern MyFitnessPal food item ID obtained from mfp_resolve_meal_food "
            "or mfp_search_food. Do not pass a meal-history history_id."
        ),
        min_length=1,
    )
    meal: str = Field(
        default="Breakfast",
        description="Meal name (e.g., 'Breakfast', 'Lunch', 'Dinner', 'Snacks')",
    )
    date: str | None = Field(
        default=None,
        description="Date in YYYY-MM-DD format. Defaults to today if not specified.",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
    amount: float = Field(
        default=1.0,
        description=(
            "Physical amount expressed in `unit`, NOT the number of database servings. "
            "Example: to log 250 grams, pass amount=250 and unit='g'."
        ),
        gt=0,
        le=5000,
    )
    unit: str = Field(
        default="serving",
        description=(
            "Unit for `amount`: use 'g', 'kg', 'oz', 'ml', an available serving "
            "name, or 'serving'. Example: amount=250, unit='g'. Italian aliases "
            "such as 'grammi' and 'porzioni' are accepted."
        ),
        min_length=1,
    )


class CreateCustomFoodInput(BaseModel):
    """Input model for creating a private custom food."""

    model_config = ConfigDict(str_strip_whitespace=True)

    description: str = Field(
        ..., description="Food name as it appears in MFP", min_length=1, max_length=200
    )
    brand_name: str = Field(
        default="Generic",
        description="Brand. Packaged food = label brand; restaurant = venue name; homemade = 'Generic'.",
        max_length=200,
    )
    serving_amount: float = Field(
        default=100, description="Serving size number (e.g. 100 for '100 g')", gt=0
    )
    serving_unit: str = Field(
        default="g", description="Serving unit (e.g. 'g', 'ml', 'piece', 'box (100 g)')"
    )
    calories: float = Field(..., description="Calories per serving", ge=0)

    carbs: float | None = Field(
        default=None,
        description="NET carbs in g (MFP adds fiber itself to report total; never pre-add fiber)",
        ge=0,
    )
    fiber: float | None = Field(default=None, description="Fiber, g", ge=0)
    sugar: float | None = Field(default=None, description="Sugars, g", ge=0)
    protein: float | None = Field(default=None, description="Protein, g", ge=0)
    fat: float | None = Field(default=None, description="Total fat, g", ge=0)
    saturated_fat: float | None = Field(default=None, description="Saturated fat, g", ge=0)
    polyunsaturated_fat: float | None = Field(
        default=None, description="Polyunsaturated fat, g", ge=0
    )
    monounsaturated_fat: float | None = Field(
        default=None, description="Monounsaturated fat, g", ge=0
    )
    trans_fat: float | None = Field(default=None, description="Trans fat, g", ge=0)
    cholesterol: float | None = Field(default=None, description="Cholesterol, mg", ge=0)
    sodium: float | None = Field(default=None, description="Sodium, mg", ge=0)
    potassium: float | None = Field(default=None, description="Potassium, mg", ge=0)
    vitamin_a: float | None = Field(default=None, description="Vitamin A, %DV", ge=0)
    vitamin_c: float | None = Field(default=None, description="Vitamin C, %DV", ge=0)
    calcium: float | None = Field(default=None, description="Calcium, %DV", ge=0)
    iron: float | None = Field(default=None, description="Iron, %DV", ge=0)

    country_code: str = Field(
        default="NL",
        description=(
            "Label convention, and it changes carb meaning. 'NL'/EU: `carbs` is read as NET "
            "(MFP reports total = carbs + fiber). Omitting/US: `carbs` is read as TOTAL. "
            "Keep 'NL' unless deliberately entering a US-style total-carb label."
        ),
        min_length=2,
        max_length=2,
    )
    public: bool = Field(
        default=False, description="Share publicly. Keep False for personal entries."
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' for human-readable or 'json' for structured data",
    )


class DeleteCustomFoodInput(BaseModel):
    """Input model for deleting a custom food."""

    model_config = ConfigDict(str_strip_whitespace=True)

    food_id: str = Field(
        ..., description="Food id (from mfp_create_custom_food or mfp_list_own_foods)", min_length=1
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' for human-readable or 'json' for structured data",
    )


class ListOwnFoodsInput(BaseModel):
    """Input model for listing the user's own custom foods."""

    model_config = ConfigDict(str_strip_whitespace=True)

    search: str = Field(default="", description="Optional substring filter on the food name")
    limit: int = Field(default=25, description="Max foods to return", gt=0, le=200)
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' for human-readable or 'json' for structured data",
    )


class RemoveFoodFromDiaryInput(BaseModel):
    """Input model for removing food entries from diary."""

    model_config = ConfigDict(extra="forbid")

    date: str | None = Field(
        default=None,
        description="Date in YYYY-MM-DD format. Defaults to today.",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
    entry_id: str | None = Field(
        default=None,
        description=(
            "Specific food_entry_id to remove. If omitted, name_contains is used to match by name."
        ),
    )
    name_contains: str | None = Field(
        default=None,
        description=(
            "Case-insensitive substring to match against entry names "
            "(e.g. 'banana' or '0.5 cup rice'). Ignored if entry_id is set."
        ),
    )
    meal: str | None = Field(
        default=None,
        description=("Restrict matching to a meal: Breakfast, Lunch, Dinner, Snacks."),
    )
    max_matches: int = Field(
        default=1,
        gt=0,
        le=50,
        description=("Safety cap on how many matching entries to delete in one call."),
    )


class SetWaterInput(BaseModel):
    """Input model for setting water intake."""

    model_config = ConfigDict(str_strip_whitespace=True)

    cups: float = Field(
        ...,
        description="Number of cups of water (e.g., 2.5 for 2.5 cups). Note: MyFitnessPal uses cups as the unit.",
        ge=0,
        le=50,
    )
    date: str | None = Field(
        default=None,
        description="Date in YYYY-MM-DD format. Defaults to today if not specified.",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
