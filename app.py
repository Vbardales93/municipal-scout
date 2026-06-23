import os
import json
import streamlit as st
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from typing import List
from fpdf import FPDF
import pypdf
from supabase import create_client, Client

# Configure the web browser tab
st.set_page_config(page_title="Municipal Scout", page_icon="🔍", layout="centered")


# --- DATA SCHEMAS FOR STRUCTURED AI OUTPUT ---
# This forces Gemini to return an unshakeable data blueprint instead of free text
class ViolationItem(BaseModel):
    item_number: int = Field(description="The sequential number of the non-compliance item or omission.")
    title: str = Field(description="The short name or title of the violation or omission.")
    rule_violated: str = Field(description="The exact code section and explicit text rule that was violated.")
    correction_needed: str = Field(
        description="The precise remediation or specification change required to pass inspection.")


class ComprehensiveAudit(BaseModel):
    summary: str = Field(
        description="A professional, introductory summary assessment written as a Municipal Zoning Officer.")
    violations: List[ViolationItem] = Field(
        description="An exhaustive collection of every single individual discrepancy caught.")


# --- INITIALIZE SECURE CLOUD DATABASE ---
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

# --- SIDEBAR / INPUT CONFIGURATION ---
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

# --- MAIN DASHBOARD INTERFACE ---
st.write("### 📄 Step 1: Upload Project Proposal")

uploaded_file = st.file_uploader(
    f"Drag and drop or browse for a contractor {selected_project.lower()} proposal (.txt or .pdf)",
    type=["txt", "pdf"]
)

if uploaded_file is not None:

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
            with st.spinner(f"Loading {selected_town} {selected_project} regulations..."):
                try:
                    town_folder = selected_town.lower().replace(" ", "_")
                    project_file = selected_project.lower()
                    rule_path = f"rules/{town_folder}/{project_file}.pdf"

                    if not os.path.exists(rule_path):
                        st.error(
                            f"⚠️ Rule book not found: Could not locate a PDF for {selected_project} regulations in {selected_town}.")
                        st.stop()

                    with open(rule_path, "rb") as file:
                        rules_pdf_bytes = file.read()

                    client = genai.Client(api_key=api_key_input)

                    system_instruction = (
                        f"You are a highly meticulous Municipal Zoning Enforcement Officer and Building Inspector in Connecticut. "
                        f"Your job is to audit residential {selected_project.lower()} permit applications against local town regulations.\n\n"
                        f"STRICT AUDIT PROTOCOL:\n"
                        f"1. Read the attached rules PDF and isolate every single distinct, numbered sub-clause, threshold, or sentence-level requirement (e.g., R326.6.1 item 1, item 2, item 8, item 8.1, item 8.2, local town rules, etc.).\n"
                        f"2. Evaluate the contractor's proposal against EVERY SINGLE isolated sub-clause independently.\n"
                        f"3. If a sub-clause requirement is directly violated OR completely unmentioned (omission), you MUST generate a unique, dedicated object inside the 'violations' array.\n"
                        f"4. CRITICAL: Never combine multiple distinct sub-clauses into a single item. Never allow a discrepancy to slide by lumping it into the description of another issue. Treat this as an absolute, itemized checklist."
                    )

                    user_prompt = f"""You are auditing this residential {selected_project.lower()} proposal for the municipality of {selected_town}, Connecticut. 

CRITICAL: Ignore any town names, cities, or zip codes written inside the contractor's proposal text. Assume this proposal has been officially submitted to the {selected_town} Building and Zoning Department. 

Audit the provided contractor proposal text strictly against the attached official town {selected_project.lower()} rules PDF document.

=== CONTRACTOR PROPOSAL ===
{application_content}"""

                    # Executing the API call with strict JSON constraint enforcement
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

                    # Parse the raw structured JSON response from Gemini
                    if response.parsed:
                        audit_data = response.parsed.model_dump()
                    else:
                        st.error("⚠️ The audit engine returned an empty response or was intercepted.")
                        with st.expander("Debug Raw API Response"):
                            st.write(response)
                        st.stop()

                    # Calculate total issues directly by counting elements in the JSON array
                    violations_list = audit_data.get("violations", [])
                    total_issues = len(violations_list)

                    # Reconstruct report_text programmatically to feed the PDF engine and UI
                    report_text = f"{audit_data.get('summary', '')}\n\n"
                    for item in violations_list:
                        report_text += f"**{item['item_number']}. {item['title']}**\n"
                        report_text += f"- The exact rule violated: {item['rule_violated']}\n"
                        report_text += f"- The precise correction needed: {item['correction_needed']}\n\n"

                    # --- LOG AUDIT RECORD TO DATABASE INTERFACE ---
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

                    # --- PREMIUM VISUAL METRICS ---
                    st.markdown("---")
                    st.write(f"### 📊 Audit Executive Summary")

                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric(label="Jurisdiction", value=selected_town)
                    with col2:
                        st.metric(label="Project Type", value=selected_project)
                    with col3:
                        st.metric(label="Non-Compliance Items", value=f"{total_issues} Found")

                    if total_issues > 0:
                        st.warning(f"Zoning Audit complete. {total_issues} compliance discrepancies must be corrected.")
                    else:
                        st.success(f"Zoning Audit complete. No blatant {selected_project.lower()} violations detected.")

                    # --- DISPLAY REPORT ON SCREEN ---
                    st.write(f"### 📋 Detailed {selected_project} Audit Results: {target_address}")
                    st.markdown(report_text)

                    # --- GENERATE PDF IN RAM ---
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

                    pdf.multi_cell(0, 6, clean_pdf_text, markdown=True)

                    raw_pdf_output = pdf.output(dest='S')
                    pdf_bytes = bytes(raw_pdf_output) if not isinstance(raw_pdf_output, str) else raw_pdf_output.encode(
                        'latin-1')

                    # --- EXPORT LINK ---
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
                    st.error(f"An error occurred during execution: {e}")

# --- REAL-TIME HISTORICAL AUDIT LOG ---
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