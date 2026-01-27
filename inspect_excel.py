import pandas as pd

file_path = "/Users/bharani/Desktop/Developer/ten/tendersontime/Sector-Subsector 25 Jan 2026.xlsx"
try:
    df = pd.read_excel(file_path)
    with open("excel_structure.txt", "w") as f:
        f.write(f"Columns: {df.columns.tolist()}\n")
        f.write(f"\nFirst 5 rows:\n")
        f.write(df.head().to_string())
        f.write(f"\n\nShape: {df.shape}\n")
    print("Analysis written to excel_structure.txt")
except Exception as e:
    print(f"Error reading file: {e}")
