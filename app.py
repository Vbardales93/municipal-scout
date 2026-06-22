import os
import streamlit as st
from google import genai
from google.genai import types
from fpdf import FPDF
import pypdf
from supabase import create_client, Client

# Configure the web browser tab
st.set_page_config(page_title="Municipal Scout", page_icon="🔍", layout="centered")

# --- INITIALIZE SECURE CLOUD DATABASE ---
# Streamlit automatically fetches these keys from your encrypted Secrets tab
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

# 1. Target Municipality Dropdown
selected_town = st.sidebar.selectbox(
    "Target Municipality",
    ["Windsor Locks", "Bristol"]
)

# 2. Project Type Dropdown
selected_project = st.sidebar.selectbox(
    "Project Category",
    ["Pool", "Deck", "Shed"]
)

# 3. Dynamic Address Input
target_address = st.sidebar.text_input("Project Address", "74 Center Street")

# 4. API Key Entry Box
api_key_input = st.sidebar.text_input("Enter Gemini API Key", type="password")

# --- MAIN DASHBOARD INTERFACE ---

st.write("### 📄 Step 1: Upload Project Proposal")

uploaded_file = st.file_uploader(
    f"Drag and drop or browse for a contractor {selected_project.lower()} proposal (.txt or .pdf)",
    type=["txt", "pdf"]
)

if uploaded_file is not None:

    # PDF vs TXT parsing logic for the contractor proposal
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

    # Expandable Preview for proposal text
    with st.expander("👀 Preview Extracted Proposal Text"):
        st.text(application_content)

    st.write("### ⚡ Step 2: Run Compliance Audit")

    if st.button("🚀 Launch Compliance Audit"):

        if not api_key_input:
            st.error("⚠️ Please enter your Gemini API Key in the sidebar to run the audit.")
        else:
            with st.spinner(f"Loading {selected_town} {selected_project} regulations..."):
                try:
                    # Dynamic path logic
                    town_folder = selected_town.lower().replace(" ", "_")
                    project_file = selected_project.lower()
                    rule_path = f"rules/{town_folder}/{project_file}.pdf"

                    if not os.path.exists(rule_path):
                        st.error(
                            f"⚠️ Rule book not found: Could not locate a PDF for {selected_project} regulations in {selected_town}.")
                        st.stop()

                    with open(rule_path, "rb") as file:
                        rules_pdf_bytes = file.read()

                    # Connect to Gemini
                    client = genai.Client(api_key=api_key_input)

                    system_instruction = (
                        f"You are a highly meticulous Municipal Zoning Enforcement Officer and Building Inspector in Connecticut. "
                        f"Your job is to audit residential {selected_project.lower()} permit applications against local town regulations. "
                        "You must be aggressively thorough. Separate every single direct violation, minor footnote conflict, "
                        "and missing detail (omission) into its own individual numbered item. Do not combine them."
                    )

                    user_prompt = f"""You are auditing this residential {selected_project.lower()} proposal for the municipality of {selected_town}, Connecticut. 

CRITICAL: Ignore any town names, cities, or zip codes written inside the contractor's proposal text. Assume this proposal has been officially submitted to the {selected_town} Building and Zoning Department. 

Audit the provided contractor proposal text strictly against the attached official town {selected_project.lower()} rules PDF document.

=== CONTRACTOR PROPOSAL ===
{application_content}

=== OUTPUT INSTRUCTIONS ===
Provide a clear introductory text summary.
Then, provide an exhaustive, completely itemized numbered list where EVERY SINGLE discrepancy, error, missing safety specification, or omission is given its own individual number. Do not bunch them together.

For every single item, format the title line in markdown bold, but keep the details underneath as plain regular text. Follow this exact template:

**Item Number. Violation Name**
- The exact rule violated: [text]
- The precise correction needed: [text]

Do not use markdown bold symbols (like **) anywhere else in the response. Keep all text plain and clean."""

                    response = client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=[
                            types.Part.from_bytes(data=rules_pdf_bytes, mime_type='application/pdf'),
                            user_prompt
                        ],
                        config=types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            temperature=0.0
                        )
                    )

                    report_text = response.text
                    total_issues = report_text.count("**") // 2

                    # --- NEW: LOG AUDIT RECORD TO DATABASE INTERFACE ---
                    with st.spinner("Logging audit metrics to secure history table..."):
                        try:
                            audit_data = {
                                "property_address": target_address,
                                "municipality": selected_town,
                                "project_category": selected_project,
                                "issues_found": total_issues,
                                "detailed_report": report_text
                            }
                            # Send data packet to Supabase
                            supabase.table("audits").insert(audit_data).execute()
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

# --- NEW: REAL-TIME HISTORICAL AUDIT LOG ---
st.markdown("---")
st.write("### 🕒 Recent Audits Log (Cloud Storage)")

try:
    # Pull the 5 most recent records from the database
    db_response = supabase.table("audits").select(
        "created_at, property_address, municipality, project_category, issues_found").order("created_at",
                                                                                            desc=True).limit(
        5).execute()

    if db_response.data:
        # Convert response records into a structured view
        log_records = []
        for row in db_response.data:
            # Clean up timestamp strings for easy reading
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