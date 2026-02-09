# services/email_import_service.py
import imaplib
import email
import streamlit as st
from sqlalchemy.orm import Session
import models


def fetch_and_process_emails(db: Session, target_branch_id: int):
    """
    Connects to Gmail and filters emails by SENDER.
    - Branch 1: Fetches emails FROM 'katkamhonda@rediffmail.com'
    - Branch 3: Fetches emails FROM 'sap.admin@honda2wheelersindia.com'
    """
    all_new_data = []
    logs = []

    # 1. Load Decoder Mappings
    mappings = db.query(models.ProductMapping).all()
    decoder_map = {
        (m.model_code.strip(), m.variant_code.strip()): (m.real_model, m.real_variant)
        for m in mappings
    }

    # 2. Find Account Config
    target_account = None
    for acc in st.secrets.email.accounts:
        b_id = acc.branch_id if hasattr(acc, 'branch_id') else acc['branch_id']
        if int(b_id) == int(target_branch_id):
            target_account = acc
            break

    if not target_account:
        return [], [f"❌ No config found for Branch ID {target_branch_id}"]

    # Unpack Credentials & SENDER FILTER
    acc_name = target_account.name if hasattr(target_account, 'name') else target_account['name']
    host = target_account.host if hasattr(target_account, 'host') else target_account['host']
    user = target_account.user if hasattr(target_account, 'user') else target_account['user']
    password = target_account.pass_ if hasattr(target_account, 'pass_') else target_account['pass']

    # CRITICAL: Get the specific sender for this branch
    target_sender = target_account.sender_filter if hasattr(target_account, 'sender_filter') else target_account.get(
        'sender_filter', '')

    if not target_sender:
        logs.append(f"❌ No 'sender_filter' defined in secrets for {acc_name}.")
        return [], logs

    logs.append(f"🔌 Connecting to Gmail...")
    logs.append(f"🎯 Searching for emails FROM: {target_sender}")

    try:
        # Use IMAP SSL (Standard for Gmail)
        with imaplib.IMAP4_SSL(host, 993) as mail:
            mail.login(user, password)
            mail.select("inbox")

            # --- THE SEARCH ---
            # Search specifically for emails FROM the target_sender
            status, messages = mail.search(None, f'(FROM "{target_sender}")')

            if status != "OK" or not messages[0]:
                logs.append(f"   ℹ️ No emails found from {target_sender}.")
                return [], logs

            email_ids = messages[0].split()

            # Scan Last 30 Emails (Reverse Order: Newest First)
            recent_ids = list(reversed(email_ids))[:30]

            s08_files_found = 0
            target_count = 5

            logs.append(f"   🔎 Scanning recent {len(recent_ids)} emails...")

            for eid in recent_ids:
                if s08_files_found >= target_count:
                    break

                try:
                    # Fetch Full Email
                    _, msg_data = mail.fetch(eid, "(RFC822)")
                    msg = email.message_from_bytes(msg_data[0][1])

                    # Extract Attachment
                    content, filename = _extract_text_attachment(msg)

                    if not content:
                        continue  # Skip emails without S08 attachment

                    s08_files_found += 1

                    # 1. Peek Load Ref
                    first_ref = _peek_load_ref(content)
                    if not first_ref:
                        logs.append(f"      ⚠️ File '{filename}' found, but no Load Ref.")
                        continue

                    # 2. Duplicate Check
                    exists = db.query(models.VehicleMaster).filter(
                        models.VehicleMaster.load_reference_number == first_ref
                    ).first()

                    if exists:
                        logs.append(f"      ⏭️ Found Load {first_ref} (Skipped - Already in DB).")
                        continue

                    # 3. Parse
                    parsed = _parse_s08_content(content, acc_name, decoder_map)
                    if parsed:
                        all_new_data.extend(parsed)
                        logs.append(f"      ✅ Found NEW Load {first_ref} ({len(parsed)} vehicles).")

                except Exception as e:
                    logs.append(f"      ⚠️ Error reading email {eid.decode()}: {e}")

            if s08_files_found == 0:
                logs.append("   ℹ️ Emails found from sender, but none had S08 attachments.")

    except Exception as e:
        logs.append(f"   ❌ Connection Error: {str(e)}")

    return all_new_data, logs


# --- HELPERS (Unchanged) ---
def _extract_text_attachment(msg):
    for part in msg.walk():
        if part.get_content_maintype() == 'multipart' or part.get('Content-Disposition') is None:
            continue
        filename = part.get_filename() or ""
        if "s08" in filename.lower() and ".txt" in filename.lower():
            try:
                return part.get_payload(decode=True).decode("utf-8", errors='ignore'), filename
            except:
                return None, None
    return None, None


def _peek_load_ref(content):
    for line in content.splitlines():
        if len(line) < 180 or line[25] != 'B': continue
        return line[84:97].strip()
    return None


def _parse_s08_content(content, source_name, decoder_map):
    batch = []
    for line in content.splitlines():
        if len(line) < 180 or line[25] != 'B': continue
        try:
            m_code = line[27:38].strip()
            v_code = line[38:45].strip()
            real_m, real_v = decoder_map.get((m_code, v_code), (m_code, v_code))

            batch.append({
                'source_account': source_name,
                'load_reference': line[84:97].strip(),
                'chassis_no': line[113:130].strip(),
                'engine_no': line[173:186].strip(),
                'color': line[45:60].strip(),
                'model': real_m,
                'variant': real_v,
            })
        except:
            continue
    return batch