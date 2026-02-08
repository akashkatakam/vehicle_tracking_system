# services/email_import_service.py
import imaplib
import email
from email.header import decode_header
import streamlit as st
import pandas as pd

EMAIL_HOST = st.secrets.get('email')['EMAIL_HOST']
EMAIL_USER = st.secrets.get('email')['EMAIL_USER']
EMAIL_PASS = st.secrets.get('email')['EMAIL_PASS']

def fetch_latest_s08_email():
    """
    Connects to Rediffmail IMAP, finds the latest email from the specified sender
    with an S08 attachment, and returns the file content.
    """
    try:
        # 1. Connect to Rediffmail IMAP
        mail = imaplib.IMAP4_SSL(EMAIL_HOST, 993)
        mail.login(EMAIL_USER, EMAIL_PASS)

        # Rediffmail uses "INBOX" (case sensitive usually)
        mail.select("INBOX")

        # 2. Search for emails from the sender
        sender = st.secrets.get('email')['EMAIL_SENDER_FILTER']
        # Search criteria: FROM sender
        status, messages = mail.search(None, f'(FROM "{sender}")')

        if status != "OK" or not messages[0]:
            return None, "No emails found from HMSI (dispatch@honda.co.in)."

        # Get the latest email (last ID in the list)
        latest_email_id = messages[0].split()[-1]
        status, msg_data = mail.fetch(latest_email_id, "(RFC822)")

        msg = email.message_from_bytes(msg_data[0][1])
        subject, encoding = decode_header(msg["Subject"])[0]
        if isinstance(subject, bytes):
            subject = subject.decode(encoding if encoding else "utf-8")

        # 3. Extract Attachment
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get("Content-Disposition") is None:
                continue

            filename = part.get_filename()
            if filename and ("s08" in filename.lower() or ".txt" in filename.lower()):
                file_content = part.get_payload(decode=True).decode("utf-8")
                return file_content, f"Successfully fetched: {filename}"

        return None, "Email found, but no S08/TXT attachment detected."

    except Exception as e:
        return None, f"Email Connection Error: {e}"


def parse_s08_content(file_content):
    """
    Parses the raw text of the S08 file into a list of dictionaries.
    Indices are calibrated to the S08S file format provided.
    """
    batch_data = []
    lines = file_content.split('\n')

    for line in lines:
        # Filter: Skip short lines or lines that don't look like vehicle records.
        # Valid records have 'B' at index 25 (the 26th character).
        if len(line) < 180 or line[25] != 'B':
            continue

        try:
            # --- EXTRACT DATA BASED ON FILE STRUCTURE ---
            # 1. Model: Indices 27 to 38
            model_code = line[27:38].strip()

            # 2. Variant: Indices 38 to 45
            variant_code = line[38:45].strip()

            # 3. Color Code: Indices 45 to 60
            color_code = line[45:60].strip()

            # 4. Chassis (VIN): Indices 113 to 130
            chassis = line[113:130].strip()

            # 5. Engine: Indices 173 to 186
            # Note: Index 172 is '0', Engine number starts at 173
            engine = line[173:186].strip()

            batch_data.append({
                'chassis_no': chassis,
                'engine_no': engine,
                'model': model_code,
                'variant': variant_code,
                'color': color_code  # This is the code (e.g., NH303)
            })
        except Exception:
            continue  # Skip malformed lines

    return batch_data