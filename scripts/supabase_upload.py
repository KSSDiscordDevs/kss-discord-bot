import csv
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from postgrest import SyncPostgrestClient

# Initialize Supabase
load_dotenv()
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_SERVICE_KEY")
supabase = SyncPostgrestClient(
    f"{url.rstrip('/')}/rest/v1",
    headers={"apikey": key, "Authorization": f"Bearer {key}"},
)
BASE_DIR = Path(__file__).resolve().parent.parent


# This class acts as your "Schema Object"
# args: string table_name:      name of the SQL table
#       string transform_func:  function used to transform the CSV file into proper SQL format
class TableSchema:
    """Describe how a CSV file should be transformed and inserted into a table."""

    def __init__(self, table_name, transform_func):
        """Store the destination table name and row transformation callback."""
        self.table_name = table_name
        self.transform_func = transform_func


# args: TableSchema schema  used for logic specific to the csv file
#       string file_path    opened and read for uploading to supabase
# returns:  void            prints appropriate success or error message
def upload_from_csv(file_path, schema: TableSchema):
    """Read a CSV file, transform its rows, and insert the results into Supabase."""
    data_to_insert = []

    try:
        with open(file_path, mode="r", encoding="utf-8") as f:
            reader = csv.reader(f)
            headers = next(reader)

            # Use the schema's custom logic to turn rows into a list of dicts
            data_to_insert = schema.transform_func(reader)

        if data_to_insert:
            supabase.table(schema.table_name).insert(data_to_insert).execute()
            print(f"✅ Uploaded {len(data_to_insert)} rows to '{schema.table_name}'")
        else:
            print(f"⚠️ No data to upload for {schema.table_name}")

    except Exception as e:
        print(f"❌ Error uploading to {schema.table_name}: {e}")


# args: csv_reader  which has already opened the csv file and is parsing it
# returns:  rows    the rows which are the parsed csv file, ready to be inserted to the supabase tables
def transform_meals(csv_reader):
    """Convert the meal schedule CSV into normalized meal records for Supabase."""
    rows = []
    meal_id = 0
    for parts in csv_reader:
        day = parts[0]
        for i in range(1, 9):
            if i < len(parts) and parts[i].strip():
                rows.append(
                    {
                        "id": meal_id,
                        "day": day,
                        "week_number": (i + 1) // 2,
                        "meal_type": "lunch" if i % 2 != 0 else "dinner",
                        "dish_name": parts[i].strip(),
                    }
                )
                meal_id += 1
    return rows


# Create the "Object" for the meals table
meals_schema = TableSchema("meals", transform_meals)
upload_from_csv(BASE_DIR / "meal_menu.csv", meals_schema)
