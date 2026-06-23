import os
import json
import streamlit as st
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from typing import List, Literal
from fpdf import FPDF
import pypdf
from supabase import create_client, Client

# Configure the web browser tab layout
st.set_page_config(page_title="Municipal Scout", page_icon="🔍", layout="centered")


# =====================================================================
# 1. DATA SCHEMAS FOR STRUCTURED AI OUTPUT (Dynamic Checklist Style)
# =====================================================================
class RuleEvaluation(BaseModel):
    rule_section: str = Field(
        description="The section or clause number discovered in the PDF (e.g., R326.6.1.1 or Local Section 12-A)")
    rule_title: str = Field(description="A short, descriptive title of the rule requirement")
    status: Literal["PASS", "VIOLATION"] = Field(
        description="Mark PASS if the proposal fully satisfies this rule. Mark VIOLATION if the proposal violates it or completely omits mandatory details.")
    exact_rule_text: str = Field(description="The verbatim or highly precise text of the rule from the PDF.")
    precise_correction_needed: str = Field(
        description="Detailed instructions for the builder on how to fix this if it is a VIOLATION. Leave blank if PASS.")


class ComprehensiveAudit(BaseModel):
    summary: str = Field(
        description="A professional, introductory summary assessment written as a Municipal Zoning Officer.")
    all_evaluations: List[RuleEvaluation] = Field(
        description="A complete checklist of EVERY single distinct rule requirement found in the rules PDF.")


# =====================================================================
# 2. INITIALIZE SECURE CLOUD DATABASE (Supabase)
# =====================================================================
try:
    supabase_url = st.secrets["SUPABASE_URL"]
    supabase_key = st.secrets["SUPABASE_KEY"]
    supabase: Client = create_client(supabase_url, supabase_key)
except Exception as e:
    st.error(f"⚠️ Cloud Database Connection Error: Ensure your Supabase Secrets are set. Details: {e}")

# --- VISUAL HEADER ---
st.title("🔍 Municipal Scout")
st.subheader("Pre-Flight Hyper-Local Zoning & Permit Auditor")
st.write("Upload a contractor's project proposal to instantly cross-reference it against official municipal rules.")
st.markdown("---")

# =====================================================================
# 3. SIDEBAR / INPUT CONFIGURATION
# =====================================================================
st.sidebar.header("Audit Configuration")

selected_town = st.sidebar.selectbox(
    "Target Municipality",
    ["Windsor Locks", "Bristol"]
)

selected_project = st.sidebar.selectbox(
    "Project Category",
    ["Pool", "Deck", "Shed"]
)

target_address = st.sidebar.text_input("Project Address", "74 Center Street")
api_key_input = st.sidebar.text_input("Enter Gemini API Key", type="password")

# =====================================================================
# 4. MAIN DASHBOARD INTERFACE (Document Ingestion)
# =====================================================================
st.write("### 📄 Step 1: Upload Project Proposal")

uploaded_file = st.file_uploader(
    f"Drag and drop or browse for a contractor {selected_project.lower()} proposal (.txt or .pdf)",
    type=["txt", "pdf"]
)

if uploaded_file is not None:
    # Handle PDF or TXT ingestion for the contractor application
    if uploaded_file.name.endswith(".pdf"):
        with st.spinner("Extracting text layers from PDF document..."):
            pdf_reader = pypdf.PdfReader(uploaded_file)
            application_content = ""
            for page in pdf_reader.pages:
                extracted_text = page.extract_text()
                if extracted_text:
                    application_content += extracted_text + "\n"
        st.success(f"Successfully extracted text from PDF ({len(pdf_reader.pages)} pages)!")
    else:
        application_content = uploaded_file.read().decode("utf-8")
        st.success("Text proposal successfully uploaded!")

    with st.expander("👀 Preview Extracted Proposal Text"):
        st.text(application_content)

    st.write("### ⚡ Step 2: Run Compliance Audit")

    if st.button("🚀 Launch Compliance Audit"):
        if not api_key_input:
            st.error("⚠️ Please enter your Gemini API Key in the sidebar to run the audit.")
        else:
            town_folder = selected_town.lower().replace(" ", "_")
            project_file = selected_project.lower()
            rule_path = f"rules/{town_folder}/{project_file}.pdf"

            if not os.path.exists(rule_path):
                st.error(
                    f"⚠️ Rule book not found: Could not locate a PDF for {selected_project} regulations in {selected_town}.")
                st.stop()

            with open(rule_path, "rb") as file:
                rules_pdf_bytes = file.read()

            with st.spinner(f"Loading local {selected_town} rules and matching checkpoints..."):
                try:
                    # Initialize the native Google GenAI Client
                    client = genai.Client(api_key=api_key_input)

                    # Core prompt guidelines for grounding the audit execution
                    system_instruction = (
                        f"You are a highly meticulous Municipal Zoning Enforcement Officer and Building Inspector in Connecticut. "
                        f"Your job is to perform a systematic, two-step audit on the attached residential {selected_project.lower()} proposal using the provided rules PDF.\n\n"
                        f"DYNAMIC AUDIT PROTOCOL:\n"
                        f"1. GENERATE THE CHECKLIST: Read the rules PDF and dynamically extract EVERY single individual requirement, parameter, threshold, and local rule. This is your temporary master checklist for this town.\n"
                        f"2. EVALUATE EVERYTHING: Evaluate the contractor's proposal against EVERY single rule you just extracted. Do not skip any.\n"
                        f"3. REPORT ALL RULES: Your output must include an entry in 'all_evaluations' for every single rule discovered. If the proposal satisfies the rule, mark it PASS. If the proposal fails the rule OR completely omits a mandatory detail (such as missing a required specification), mark it VIOLATION.\n"
                        f"4. CRITICAL: Never bundle separate rules together. Keep your discovered checklist completely atomic to guarantee identical item counts across separate runs of this same PDF."
                    )

                    user_prompt = f"""You are auditing this residential {selected_project.lower()} proposal for the municipality of {selected_town}, Connecticut. 

CRITICAL: Ignore any town names, cities, or zip codes written inside the contractor's proposal text. Assume this proposal has been officially submitted to the {selected_town} Building and Zoning Department. 

Audit the provided contractor proposal text strictly against the attached official town {selected_project.lower()} rules PDF document.

=== CONTRACTOR PROPOSAL ===
{application_content}"""

                    # Execute the Structured API call
                    response = client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=[
                            types.Part.from_bytes(data=rules_pdf_bytes, mime_type='application/pdf'),
                            user_prompt
                        ],
                        config=types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            temperature=0.0,
                            response_mime_type="application/json",
                            response_schema=ComprehensiveAudit,
                        )
                    )

                    # Unpack the structured Pydantic return object
                    if response.parsed:
                        audit_data = response.parsed.model_dump()
                    else:
                        st.error("⚠️ The audit engine returned an empty response or was intercepted.")
                        st.stop()

                    # Split evaluations for custom reporting
                    all_items = audit_data.get("all_evaluations", [])
                    violations = [item for item in all_items if item["status"] == "VIOLATION"]
                    passes = [item for item in all_items if item["status"] == "PASS"]
                    total_issues = len(violations)

                    # Reconstruct readable report plain text format to pass cleanly to database logs and FPDF
                    report_text = f"{audit_data.get('summary', '')}\n\n"
                    report_text += f"=== IDENTIFIED CODE VIOLATIONS & OMISSIONS ({total_issues} Total) ===\n\n"
                    for idx, item in enumerate(violations, 1):
                        report_text += f"{idx}. {item['rule_title']} ({item['rule_section']})\n"
                        report_text += f"- Rule text: {item['exact_rule_text']}\n"
                        report_text += f"- Required Correction: {item['precise_correction_needed']}\n\n"

                    # --- LOG AUDIT RECORD TO DATABASE ---
                    with st.spinner("Logging audit metrics to secure history table..."):
                        try:
                            audit_db_packet = {
                                "property_address": target_address,
                                "municipality": selected_town,
                                "project_category": selected_project,
                                "issues_found": total_issues,
                                "detailed_report": report_text
                            }
                            supabase.table("audits").insert(audit_db_packet).execute()
                        except Exception as db_err:
                            st.warning(f"⚠️ Audit completed but failed to log to cloud history: {db_err}")

                    # --- RENDER SCREEN DASHBOARD INTERFACE ---
                    st.markdown("---")
                    st.write(f"### 📊 Audit Executive Summary")

                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric(label="Total Rules Verified", value=len(all_items))
                    with col2:
                        st.metric(label="Rules Compliant", value=len(passes))
                    with col3:
                        st.metric(label="Non-Compliance Items", value=f"{total_issues} Found")

                    st.write(audit_data.get("summary", ""))

                    # Display Violations Block
                    st.write(f"### 📋 Detailed {selected_project} Audit Results: {target_address}")
                    if total_issues == 0:
                        st.success(f"🎉 Zoning Audit complete. No blatant code discrepancies detected.")
                    else:
                        for idx, v in enumerate(violations, 1):
                            st.warning(f"**{idx}. {v['rule_title']} ({v['rule_section']})**")
                            st.markdown(f"- **The exact rule:** {v['exact_rule_text']}")
                            st.markdown(f"- **The precise correction needed:** {v['precise_correction_needed']}")
                            st.markdown("---")

                    # Expandable Passes Block
                    with st.expander("✅ View Fully Compliant Checklist Items"):
                        for p in passes:
                            st.markdown(f"**🟢 {p['rule_title']} ({p['rule_section']})** — *Compliant*")
                            st.caption(f"Verified condition: {p['exact_rule_text']}")

                    # --- GENERATE COMPLIANT PDF IN MEMORY VIA FPDF ---
                    pdf = FPDF()
                    pdf.add_page()
                    pdf.set_auto_page_break(auto=True, margin=15)

                    pdf.set_font("Helvetica", "B", 16)
                    pdf.set_text_color(20, 40, 80)
                    pdf.cell(0, 10, f"MUNICIPAL SCOUT: {selected_project.upper()} ZONING AUDIT", new_x="LMARGIN",
                             new_y="NEXT", align="C")

                    pdf.set_font("Helvetica", "I", 10)
                    pdf.set_text_color(100, 100, 100)
                    pdf.cell(0, 10, f"Target Property: {target_address}, {selected_town}, CT", new_x="LMARGIN",
                             new_y="NEXT", align="C")
                    pdf.ln(5)

                    pdf.set_draw_color(20, 40, 80)
                    pdf.line(10, 32, 200, 32)
                    pdf.ln(5)

                    pdf.set_font("Helvetica", "", 11)
                    pdf.set_text_color(0, 0, 0)

                    clean_pdf_text = (
                        report_text
                        .replace('“', '"').replace('”', '"')
                        .replace('’', "'").replace('‘', "'")
                        .replace('—', '-').replace('–', '-')
                    )

                    pdf.multi_cell(0, 6, clean_pdf_text)

                    raw_pdf_output = pdf.output(dest='S')
                    pdf_bytes = bytes(raw_pdf_output) if not isinstance(raw_pdf_output, str) else raw_pdf_output.encode(
                        'latin-1')

                    # --- DOWNLOAD UTILITY ACTION LINK ---
                    st.markdown("---")
                    st.write("### 💾 Step 3: Export Report")
                    clean_filename = f"{selected_town.lower().replace(' ', '_')}_{project_file}_audit_report.pdf"

                    st.download_button(
                        label="📥 Download Printable PDF Report",
                        data=pdf_bytes,
                        file_name=clean_filename,
                        mime="application/pdf"
                    )

                except Exception as e:
                    if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                        st.error(
                            "⏳ **Gemini API Cooldown Active:** Free tier quota reached. Please wait a short moment and try executing again.")
                    else:
                        st.error(f"An error occurred during execution: {e}")

# =====================================================================
# 5. REAL-TIME HISTORICAL CLOUD LOG SHELF (Supabase Feed)
# =====================================================================
st.markdown("---")
st.write("### 🕒 Recent Audits Log (Cloud Storage)")

try:
    db_response = supabase.table("audits").select(
        "created_at, property_address, municipality, project_category, issues_found").order("created_at",
                                                                                            desc=True).limit(
        5).execute()

    if db_response.data:
        log_records = []
        for row in db_response.data:
            formatted_date = row["created_at"].split("T")[0]
            log_records.append({
                "Date": formatted_date,
                "Property Address": row["property_address"],
                "Town": row["municipality"],
                "Category": row["project_category"],
                "Issues Detected": row["issues_found"]
            })
        st.table(log_records)
    else:
        st.info("No past audits logged yet. Run your first audit above to populate the history shelf!")
except Exception as log_err:
    st.caption(f"Could not load database records: {log_err}")