
ENRICHMENT_PROMPT = """
You are an expert Tender Analyst. Your goal is to structure and enrich procurement data for a high-precision search engine.

Analyze the following tender:
Title: {title}
Description: {description}

## TASKS

1. **Domain & Category Assignment**:
   - Assign a `core_domain` from: [Healthcare, Infrastructure, IT, Energy, Defense, Education, Agriculture, Transport].
   - Assign a `procurement_type` from: [Works, Supply, Services, Consultancy].
   - **CRITICAL**: Distinguish between "Hospital Construction" (Infrastructure) and "Medical Equipment" (Healthcare).
     - IF "Construction of Hospital" -> Domain: **Infrastructure**, Secondary: Healthcare.
     - IF "Supply of MRI Machine" -> Domain: **Healthcare**.

2. **Semantic Expansion (The "Relatedness Map")**:
   - Generate `search_keywords` to help users find this tender even if they search for related terms.
   - **Logic**:
     - IF "Hospital" -> Add: "Clinic, Nursing Home, Dispensary, Medical Center, Healthcare Facility".
     - IF "Road" -> Add: "Highway, Pavement, Driveway, Street, Asphalt".
     - IF "School" -> Add: "College, University, Classroom, Educational Institute".
   - Include specific item names if generic (e.g., "IT Equipment" -> "Laptop, Desktop, Server, Printer").

3. **Entity Extraction**:
   - Extract `authority_name`: The organization issuing the tender (e.g., AIIMS, NHAI, PWD, CPWD). If not found, use "Unknown".
   - Extract `location_city`: The specific city or district.
   - Extract `location_state`: The state or province.

4. **Title Correction**:
   - Correct any spelling or grammatical errors in the original `Title`.

5. **Signal Summary**:
   - Create a clean 5-10 word summary of the core requirement (Action + Object). Removing admin jargon.

## OUTPUT SCHEMA (JSON ONLY)
{{
  "core_domain": "String",
  "procurement_type": "String",
  "search_keywords": ["String", "String"],
  "entities": {{
    "authority_name": "String",
    "location_city": "String",
    "location_state": "String"
  }},
  "signal_summary": "String"
}}
"""
