# services/email_import_service.py
import imaplib
import email
import pandas as pd
from sqlalchemy.orm import Session
import models


def fetch_and_process_emails(db: Session, target_branch_id: int, color_map: dict = None, progress_callback=None):
    """
    Connects to Gmail and filters emails by SENDER.
    Accepts a color_map to translate OEM codes to readable colors.
    Accepts a progress_callback(str) to update the UI in real-time.
    """
    all_new_data = []
    logs = []

    def log(msg):
        logs.append(msg)
        if progress_callback:
            progress_callback(msg)

    # 1. Load Decoder Mappings
    mappings = db.query(models.ProductMapping).all()
    decoder_map = {
        (m.model_code.strip(), m.variant_code.strip()): (m.real_model, m.real_variant)
        for m in mappings
    }

    # 2. Find Account Config
    target_account = None
    # Streamlit secrets access (assuming imported or available in context, passed implicitly via runtime)
    # Note: In a pure service, we might pass config in, but sticking to existing pattern:
    import streamlit as st
    for acc in st.secrets.email.accounts:
        b_id = acc.branch_id if hasattr(acc, 'branch_id') else acc['branch_id']
        if int(b_id) == int(target_branch_id):
            target_account = acc
            break

    if not target_account:
        return [], [f"❌ No config found for Branch ID {target_branch_id}"]

    # Unpack Credentials
    acc_name = target_account.name if hasattr(target_account, 'name') else target_account['name']
    host = target_account.host if hasattr(target_account, 'host') else target_account['host']
    user = target_account.user if hasattr(target_account, 'user') else target_account['user']
    password = target_account.pass_ if hasattr(target_account, 'pass_') else target_account['pass']
    target_sender = target_account.sender_filter if hasattr(target_account, 'sender_filter') else target_account.get(
        'sender_filter', '')

    if not target_sender:
        log(f"❌ No 'sender_filter' defined for {acc_name}.")
        return [], logs

    log(f"🔌 Connecting to {host}...")

    try:
        with imaplib.IMAP4_SSL(host, 993) as mail:
            mail.login(user, password)
            mail.select("inbox")

            log(f"🎯 Searching emails from: {target_sender}")
            status, messages = mail.search(None, f'(FROM "{target_sender}")')

            if status != "OK" or not messages[0]:
                log(f"ℹ️ No emails found from target sender.")
                return [], logs

            email_ids = messages[0].split()
            # Scan Last 30 Emails
            recent_ids = list(reversed(email_ids))[:30]

            log(f"🔎 Found {len(email_ids)} emails. Scanning recent {len(recent_ids)}...")

            s08_files_found = 0
            target_count = 5

            for idx, eid in enumerate(recent_ids):
                if s08_files_found >= target_count:
                    break

                # Feedback every few emails
                if idx % 5 == 0:
                    log(f"   ⏳ Scanning email {idx + 1}/{len(recent_ids)}...")

                try:
                    _, msg_data = mail.fetch(eid, "(RFC822)")
                    msg = email.message_from_bytes(msg_data[0][1])
                    content, filename = _extract_text_attachment(msg)

                    if not content:
                        continue

                    s08_files_found += 1

                    # 1. Peek Load Ref
                    first_ref = _peek_load_ref(content)
                    if not first_ref:
                        log(f"      ⚠️ Skipped {filename}: No Load Ref found.")
                        continue

                    # 2. Duplicate Check
                    exists = db.query(models.VehicleMaster).filter(
                        models.VehicleMaster.load_reference_number == first_ref
                    ).first()

                    if exists:
                        log(f"      ⏭️ Skipped Load {first_ref} (Already in DB).")
                        continue

                    # 3. Parse with Color Map
                    parsed = _parse_s08_content(content, acc_name, decoder_map, color_map)
                    if parsed:
                        all_new_data.extend(parsed)
                        log(f"      ✅ Imported Load {first_ref} ({len(parsed)} vehicles).")

                except Exception as e:
                    log(f"      ⚠️ Error parsing email {eid.decode()}: {e}")

            if s08_files_found == 0:
                log("   ℹ️ No S08 attachments found in recent emails.")
            else:
                log(f"✨ Scan complete. Found {len(all_new_data)} new vehicles.")

    except Exception as e:
        log(f"❌ Connection Error: {str(e)}")

    return all_new_data, logs


# --- HELPERS ---
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


def _parse_s08_content(content, source_name, decoder_map, color_map=None):
    batch = []
    for line in content.splitlines():
        if len(line) < 180 or line[25] != 'B': continue
        try:
            m_code = line[27:38].strip()
            v_code = line[38:45].strip()
            real_m, real_v = decoder_map.get((m_code, v_code), (m_code, v_code))

            # Color Mapping Logic
            raw_color_code = line[45:60].strip()
            final_color = raw_color_code
            if color_map:
                # Try direct match or stripping spaces
                final_color = color_map.get(raw_color_code, raw_color_code)

            batch.append({
                'source_account': source_name,
                'load_reference': line[84:97].strip(),
                'chassis_no': line[113:130].strip(),
                'engine_no': line[173:186].strip(),
                'color': final_color,  # Use readable color
                'model': real_m,
                'variant': real_v,
            })
        except:
            continue
    return batch