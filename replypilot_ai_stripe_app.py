import streamlit as st
import json
import os
import re
import html
from datetime import datetime
from openai import OpenAI
import stripe

# =========================
# PAGE CONFIG
# =========================
st.set_page_config(
    page_title="ReplyPilot AI",
    page_icon="🤖",
    layout="wide"
)

# =========================
# CUSTOM CSS
# =========================
st.markdown("""
<style>
    .block-container {
    padding-top: 2.5rem;
    padding-bottom: 2rem;}
   .app-title {
    font-size: 2.2rem;
    font-weight: 800;
    line-height: 1.35;
    padding-top: 0.25rem;
    padding-bottom: 0.25rem;
    margin-bottom: 0.3rem;}
    .app-subtitle { color: #64748b; font-size: 1rem; margin-bottom: 1.5rem; }
    .card {
        background: #ffffff;
        padding: 1.25rem;
        border-radius: 18px;
        border: 1px solid #e5e7eb;
        box-shadow: 0 8px 24px rgba(15, 23, 42, 0.05);
        margin-bottom: 1rem;
    }
    .metric-card {
        background: linear-gradient(135deg, #f8fafc, #eef2ff);
        padding: 1rem;
        border-radius: 16px;
        border: 1px solid #e5e7eb;
        text-align: center;
    }
    .price-card {
        background: #ffffff;
        padding: 1.5rem;
        border-radius: 20px;
        border: 1px solid #e5e7eb;
        box-shadow: 0 12px 32px rgba(15, 23, 42, 0.06);
        min-height: 300px;
    }
    .price-card-pro {
        border: 2px solid #2563eb;
        background: linear-gradient(135deg, #ffffff, #eff6ff);
    }
    .small-muted { color: #64748b; font-size: 0.9rem; }
    .tag {
        display: inline-block;
        background: #eef2ff;
        color: #3730a3;
        padding: 0.25rem 0.55rem;
        border-radius: 999px;
        font-size: 0.75rem;
        font-weight: 700;
        margin-right: 0.3rem;
        margin-bottom: 0.3rem;
    }
    .urgent-tag { background: #fee2e2; color: #991b1b; }
    .refund-tag { background: #ffedd5; color: #9a3412; }
    .vip-tag { background: #dcfce7; color: #166534; }
    .shipping-tag { background: #dbeafe; color: #1e40af; }
    div.stButton > button { border-radius: 12px; font-weight: 700; height: 2.75rem; }
    textarea { border-radius: 12px !important; }
    input { border-radius: 12px !important; }
</style>
""", unsafe_allow_html=True)

# =========================
# FILES + LIMITS
# =========================
CONFIG_FILE = "config.json"
USERS_FILE = "users.json"
USAGE_FILE = "usage.json"
HISTORY_FILE = "history.json"

FREE_USAGE_LIMIT = 30
PRO_USAGE_LIMIT = 1000

# =========================
# SECRETS / ENVIRONMENT
# =========================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID")
APP_URL = os.getenv("APP_URL", "http://localhost:8501")
APP_PASSWORD = os.getenv("APP_PASSWORD", "demo123")

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# =========================
# SESSION STATE
# =========================
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "reply" not in st.session_state:
    st.session_state.reply = ""
if "bulk_results" not in st.session_state:
    st.session_state.bulk_results = []
if "current_user" not in st.session_state:
    st.session_state.current_user = ""

# =========================
# JSON HELPERS
# =========================
def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def ensure_config_shape(config):
    if "products" not in config:
        config["products"] = []
    for product in config["products"]:
        product.setdefault("name", "")
        product.setdefault("shipping", "")
        product.setdefault("returns", "")
        product.setdefault("templates", [])
    return config

def get_user_record(users, username):
    users.setdefault(username, {
        "plan": "free",
        "stripe_customer_id": "",
        "stripe_subscription_id": "",
        "paid_until": "",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })
    return users[username]

def is_pro_user(user_record):
    return user_record.get("plan") == "pro"

def user_usage_key(username):
    return username or "anonymous"

# =========================
# STRIPE HELPERS
# =========================
def create_checkout_url(username, user_record):
    if not STRIPE_SECRET_KEY or not STRIPE_PRICE_ID:
        raise ValueError("Stripe is not configured. Add STRIPE_SECRET_KEY and STRIPE_PRICE_ID to Streamlit Secrets.")

    customer_id = user_record.get("stripe_customer_id") or None

    session_args = {
        "mode": "subscription",
        "line_items": [{"price": STRIPE_PRICE_ID, "quantity": 1}],
        "success_url": f"{APP_URL}?checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{APP_URL}?checkout=cancel",
        "client_reference_id": username,
        "metadata": {"username": username},
        "allow_promotion_codes": True,
    }

    if customer_id:
        session_args["customer"] = customer_id

    session = stripe.checkout.Session.create(**session_args)
    return session.url

def create_billing_portal_url(user_record):
    if not STRIPE_SECRET_KEY:
        raise ValueError("Stripe is not configured. Add STRIPE_SECRET_KEY to Streamlit Secrets.")

    customer_id = user_record.get("stripe_customer_id")
    if not customer_id:
        raise ValueError("No Stripe customer found yet. Upgrade first.")

    portal = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=APP_URL,
    )
    return portal.url

def apply_checkout_success_from_query(users, username):
    """MVP upgrade verification after Stripe redirects back.
    For production, use Stripe webhooks as source of truth.
    """
    params = st.query_params
    if params.get("checkout") == "success" and params.get("session_id"):
        session_id = params.get("session_id")

        try:
            session = stripe.checkout.Session.retrieve(session_id)
            if session and session.get("status") == "complete":
                user_record = get_user_record(users, username)
                user_record["plan"] = "pro"
                user_record["stripe_customer_id"] = session.get("customer", "") or user_record.get("stripe_customer_id", "")
                user_record["stripe_subscription_id"] = session.get("subscription", "") or user_record.get("stripe_subscription_id", "")
                save_json(USERS_FILE, users)

                st.success("✅ Upgrade successful. Your account is now Pro.")
                st.query_params.clear()
                st.rerun()
        except Exception as e:
            st.warning(f"Could not verify checkout session: {e}")

# =========================
# OPENAI HELPERS
# =========================
client = OpenAI(api_key=OPENAI_API_KEY)

def parse_confidence(text):
    match = re.search(r"confidence\s*:\s*(\d+)", text, re.IGNORECASE)
    if match:
        value = int(match.group(1))
        return max(0, min(100, value))
    return 50

def parse_field(text, field_name, fallback=""):
    pattern = rf"{field_name}\s*:\s*(.+)"
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return fallback

def auto_tag(message):
    msg = message.lower()
    tags = []
    if any(word in msg for word in ["refund", "money back", "return my money"]):
        tags.append("REFUND")
    if any(word in msg for word in ["broken", "damaged", "cracked", "defective"]):
        tags.append("DAMAGED")
    if any(word in msg for word in ["late", "tracking", "where is", "not arrived", "delivery"]):
        tags.append("SHIPPING")
    if any(word in msg for word in ["angry", "terrible", "bad review", "unacceptable", "very upset"]):
        tags.append("URGENT")
    if any(word in msg for word in ["repeat customer", "loyal customer", "bulk order", "wholesale"]):
        tags.append("VIP")
    if not tags:
        tags.append("GENERAL")
    return tags

def render_tags(tags):
    html_tags = ""
    for tag in tags:
        css_class = "tag"
        if tag == "URGENT":
            css_class += " urgent-tag"
        elif tag == "REFUND":
            css_class += " refund-tag"
        elif tag == "VIP":
            css_class += " vip-tag"
        elif tag == "SHIPPING":
            css_class += " shipping-tag"
        html_tags += f"<span class='{css_class}'>{tag}</span>"
    st.markdown(html_tags, unsafe_allow_html=True)

def detect_product_and_intent(message, product_list):
    product_names = "\n".join(product_list)
    prompt = f"""
You are an e-commerce support classifier.

Identify:
1. The most likely product from the product list.
2. The customer intent.
3. A confidence score from 0 to 100.

Intent must be one of:
- damaged
- shipping
- refund
- general

Products:
{product_names}

Customer message:
{message}

Return exactly this format:
product: <product name>
intent: <intent>
confidence: <0-100>
"""
    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content

def generate_replies(message, matched_product, intent, tone, template_text):
    prompt = f"""
You are a professional e-commerce seller.

Product:
{matched_product["name"]}

Shipping policy:
{matched_product["shipping"]}

Return policy:
{matched_product["returns"]}

Customer intent:
{intent}

Tone:
{tone}

Saved template to use as base, if helpful:
{template_text}

Generate 3 different reply options.

Rules:
- Each reply must be under 80 words.
- Match the requested tone.
- Be clear, helpful, and human.
- Apologize when appropriate.
- Do not promise anything outside the listed policy.
- If the issue seems risky, keep the reply safe and suggest reviewing details.

Format:
Option 1:
...

Option 2:
...

Option 3:
...

Customer message:
{message}
"""
    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content

def process_message(message, config, selected_product, tone, template_text):
    product_names = [p["name"] for p in config["products"]]
    tags = auto_tag(message)

    detection = detect_product_and_intent(message, product_names)

    detected_product_name = parse_field(detection, "product", selected_product["name"])
    intent = parse_field(detection, "intent", "general").lower()
    confidence = parse_confidence(detection)

    matched_product = next(
        (p for p in config["products"] if p["name"].lower() in detected_product_name.lower()),
        selected_product
    )

    reply = generate_replies(
        message=message,
        matched_product=matched_product,
        intent=intent,
        tone=tone,
        template_text=template_text
    )

    needs_human_review = confidence < 85 or "URGENT" in tags or "REFUND" in tags

    return {
        "message": message,
        "reply": reply,
        "product": matched_product["name"],
        "intent": intent,
        "confidence": confidence,
        "tags": tags,
        "needs_human_review": needs_human_review,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

def copy_button(text, label="📋 Copy to Clipboard"):
    safe_text = html.escape(text).replace("`", "\\`")
    st.markdown(
        f"""
        <button
            style="
                width:100%;
                border:none;
                border-radius:12px;
                padding:0.8rem 1rem;
                background:#2563eb;
                color:white;
                font-weight:700;
                cursor:pointer;
                margin-top:0.5rem;
            "
            onclick="navigator.clipboard.writeText(`{safe_text}`)"
        >
            {label}
        </button>
        """,
        unsafe_allow_html=True
    )

# =========================
# LOGIN
# =========================
def login_screen():
    st.markdown("""
    <div style="max-width: 700px; margin: 4rem auto 2rem auto; text-align: center;">
        <div style="font-size: 3rem;">🤖</div>
        <div class="app-title">ReplyPilot AI</div>
        <div class="app-subtitle">AI customer support assistant for e-commerce sellers</div>
    </div>
    """, unsafe_allow_html=True)

    with st.container(border=True):
        st.markdown("### 🔐 Login")
        st.caption("Demo login. Use a store name + your app password.")

        username = st.text_input("Store name or username")
        password = st.text_input("Password", type="password")

        if st.button("Login", use_container_width=True, type="primary"):
            if password == APP_PASSWORD and username.strip():
                st.session_state.logged_in = True
                st.session_state.current_user = username.strip()
                st.rerun()
            else:
                st.error("Incorrect password or missing username.")

if not st.session_state.logged_in:
    login_screen()
    st.stop()

# =========================
# LOAD DATA
# =========================
config = ensure_config_shape(load_json(CONFIG_FILE, {"products": []}))
users = load_json(USERS_FILE, {})
usage = load_json(USAGE_FILE, {})
history_data = load_json(HISTORY_FILE, [])

current_user = st.session_state.current_user
user_record = get_user_record(users, current_user)
save_json(USERS_FILE, users)

if STRIPE_SECRET_KEY:
    apply_checkout_success_from_query(users, current_user)
    users = load_json(USERS_FILE, {})
    user_record = get_user_record(users, current_user)

usage_key = user_usage_key(current_user)
usage.setdefault(usage_key, {"count": 0})
user_count = usage[usage_key].get("count", 0)
is_pro = is_pro_user(user_record)
usage_limit = PRO_USAGE_LIMIT if is_pro else FREE_USAGE_LIMIT

# =========================
# SIDEBAR
# =========================
with st.sidebar:
    st.markdown("## 🤖 ReplyPilot AI")
    st.caption("E-commerce support copilot")

    page = st.radio(
        "Navigation",
        ["Dashboard", "Products", "Reply Generator", "History", "Upgrade"],
        label_visibility="collapsed"
    )

    st.divider()

    st.markdown("### 👤 Account")
    st.write(current_user)

    plan_label = "Pro" if is_pro else "Free"
    if is_pro:
        st.success(f"Plan: {plan_label}")
    else:
        st.info(f"Plan: {plan_label}")

    st.markdown("### 📊 Usage")
    st.progress(min(user_count / usage_limit, 1.0))
    st.caption(f"{user_count} / {usage_limit} generations used")

    if not is_pro:
        st.caption(f"{max(0, usage_limit - user_count)} free generations remaining")

    if user_count >= usage_limit:
        st.warning("Usage limit reached. Upgrade required.")

    st.divider()

    if is_pro and st.button("Manage Billing", use_container_width=True):
        try:
            url = create_billing_portal_url(user_record)
            st.link_button("Open Billing Portal", url, use_container_width=True)
        except Exception as e:
            st.error(str(e))

    if st.button("Logout", use_container_width=True):
        st.session_state.logged_in = False
        st.session_state.current_user = ""
        st.rerun()

# =========================
# HEADER
# =========================
st.markdown("""
<div class="app-title">🤖 ReplyPilot AI</div>
<div class="app-subtitle">Premium AI customer support assistant for e-commerce sellers</div>
""", unsafe_allow_html=True)

# =========================
# DASHBOARD PAGE
# =========================
if page == "Dashboard":
    m1, m2, m3, m4 = st.columns(4)

    with m1:
        st.markdown(f"<div class='metric-card'><h2>{len(config['products'])}</h2><p>Products</p></div>", unsafe_allow_html=True)
    with m2:
        total_templates = sum(len(p.get("templates", [])) for p in config["products"])
        st.markdown(f"<div class='metric-card'><h2>{total_templates}</h2><p>Templates</p></div>", unsafe_allow_html=True)
    with m3:
        st.markdown(f"<div class='metric-card'><h2>{user_count}</h2><p>Replies Generated</p></div>", unsafe_allow_html=True)
    with m4:
        st.markdown(f"<div class='metric-card'><h2>{plan_label}</h2><p>Current Plan</p></div>", unsafe_allow_html=True)

    st.markdown(" ")
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.markdown("### 🚀 Quick Start")
    st.write("1. Add products and policies in **Products**.")
    st.write("2. Create reusable templates for common issues.")
    st.write("3. Generate replies in **Reply Generator**.")
    st.write("4. Upgrade to Pro for a larger monthly usage limit.")
    st.markdown("</div>", unsafe_allow_html=True)

# =========================
# PRODUCTS PAGE
# =========================
elif page == "Products":
    left, right = st.columns([1, 1.2])

    with left:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown("### 📦 Add Product")

        name = st.text_input("Product Name")
        ship = st.text_input("Shipping Policy", placeholder="Example: Ships in 3–5 business days")
        ret = st.text_input("Return Policy", placeholder="Example: 30-day return policy")

        if st.button("Add Product", use_container_width=True, type="primary"):
            if not name.strip():
                st.warning("Please enter a product name.")
            else:
                config["products"].append({
                    "name": name.strip(),
                    "shipping": ship.strip(),
                    "returns": ret.strip(),
                    "templates": []
                })
                save_json(CONFIG_FILE, config)
                st.success("Product added!")
                st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    with right:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown("### 🧰 Manage Products")

        if not config["products"]:
            st.info("No products yet. Add your first product.")
        else:
            for i, product in enumerate(config["products"]):
                product.setdefault("templates", [])
                with st.expander(product["name"]):
                    edit_name = st.text_input("Name", value=product["name"], key=f"edit_name_{i}")
                    edit_shipping = st.text_input("Shipping", value=product["shipping"], key=f"edit_shipping_{i}")
                    edit_returns = st.text_input("Returns", value=product["returns"], key=f"edit_returns_{i}")

                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button("Save Product", key=f"save_product_{i}", use_container_width=True):
                            config["products"][i]["name"] = edit_name
                            config["products"][i]["shipping"] = edit_shipping
                            config["products"][i]["returns"] = edit_returns
                            save_json(CONFIG_FILE, config)
                            st.success("Saved!")
                    with c2:
                        if st.button("Delete Product", key=f"delete_product_{i}", use_container_width=True):
                            config["products"].pop(i)
                            save_json(CONFIG_FILE, config)
                            st.rerun()

                    st.divider()
                    st.markdown("#### 📄 Saved Templates")

                    tpl_title = st.text_input("Template Title", key=f"tpl_title_{i}")
                    tpl_text = st.text_area("Template Text", key=f"tpl_text_{i}")

                    if st.button("Add Template", key=f"add_template_{i}", use_container_width=True):
                        if tpl_title.strip() and tpl_text.strip():
                            config["products"][i]["templates"].append({
                                "title": tpl_title.strip(),
                                "text": tpl_text.strip()
                            })
                            save_json(CONFIG_FILE, config)
                            st.success("Template added!")
                            st.rerun()
                        else:
                            st.warning("Please enter both title and text.")

                    for t_idx, tpl in enumerate(config["products"][i].get("templates", [])):
                        tc1, tc2 = st.columns([3, 1])
                        with tc1:
                            st.write(f"📄 **{tpl['title']}**")
                        with tc2:
                            if st.button("Delete", key=f"delete_tpl_{i}_{t_idx}"):
                                config["products"][i]["templates"].pop(t_idx)
                                save_json(CONFIG_FILE, config)
                                st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

# =========================
# REPLY GENERATOR PAGE
# =========================
elif page == "Reply Generator":
    if not config["products"]:
        st.warning("Please add at least one product first in the Products page.")
        st.stop()

    col_left, col_right = st.columns([1, 1.4])

    with col_left:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown("### ⚙️ Reply Settings")

        product_names = [p["name"] for p in config["products"]]
        selected_name = st.selectbox("Default Product", product_names)
        selected_product = next(p for p in config["products"] if p["name"] == selected_name)

        mode = st.radio("Mode", ["Single", "Bulk"], horizontal=True)
        tone = st.selectbox("Tone", ["Friendly", "Professional", "Luxury", "Firm"])

        template_text = ""
        templates = selected_product.get("templates", [])
        if templates:
            chosen_template = st.selectbox("Saved Template", ["None"] + [t["title"] for t in templates])
            if chosen_template != "None":
                template_text = next(t["text"] for t in templates if t["title"] == chosen_template)
                st.text_area("Template Preview", template_text, height=120)
        else:
            st.caption("No templates saved for this product yet.")

        st.markdown("</div>", unsafe_allow_html=True)

    with col_right:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown("### 💬 Customer Message")

        if mode == "Single":
            customer_message = st.text_area(
                "Paste customer message",
                height=160,
                placeholder="Example: My order arrived damaged. What can you do?"
            )
        else:
            bulk_input = st.text_area(
                "Paste multiple messages, one per line",
                height=220,
                placeholder="Message 1\nMessage 2\nMessage 3"
            )

        limit_reached = user_count >= usage_limit
        if limit_reached:
            st.error("Usage limit reached. Please upgrade.")

        if st.button("✨ Generate Replies", use_container_width=True, type="primary", disabled=limit_reached):
            try:
                if mode == "Single":
                    if not customer_message.strip():
                        st.warning("Please paste a customer message.")
                        st.stop()

                    result = process_message(customer_message, config, selected_product, tone, template_text)
                    st.session_state.reply = result["reply"]
                    st.session_state.bulk_results = [result]
                    history_data.append(result)
                    save_json(HISTORY_FILE, history_data)

                    usage[usage_key]["count"] += 1
                    save_json(USAGE_FILE, usage)

                else:
                    messages = [m.strip() for m in bulk_input.split("\n") if m.strip()]
                    if not messages:
                        st.warning("Please paste at least one message.")
                        st.stop()
                    if len(messages) > 10:
                        st.warning("Bulk mode supports up to 10 messages at a time.")
                        st.stop()
                    if user_count + len(messages) > usage_limit:
                        st.warning("This batch would exceed your usage limit. Upgrade or reduce message count.")
                        st.stop()

                    results = []
                    for msg in messages:
                        result = process_message(msg, config, selected_product, tone, template_text)
                        results.append(result)
                        history_data.append(result)
                        usage[usage_key]["count"] += 1

                    save_json(HISTORY_FILE, history_data)
                    save_json(USAGE_FILE, usage)

                    st.session_state.bulk_results = results
                    st.session_state.reply = "\n\n---\n\n".join(
                        [f"Customer: {r['message']}\n\n{r['reply']}" for r in results]
                    )

                st.success("Replies generated!")
                st.rerun()

            except Exception as e:
                st.error(f"Error: {e}")

        st.markdown("</div>", unsafe_allow_html=True)

    if st.session_state.bulk_results:
        st.markdown("### 📝 AI Replies")
        for idx, result in enumerate(st.session_state.bulk_results, start=1):
            with st.container(border=True):
                st.markdown(f"#### Reply {idx}")
                st.caption(f"Product: {result['product']} | Intent: {result['intent']} | Confidence: {result['confidence']}%")
                render_tags(result["tags"])

                if result["needs_human_review"]:
                    st.error("🚨 Needs Human Review")
                else:
                    st.success("✅ Safe to Review & Send")

                st.text_area("Reply", result["reply"], height=220, key=f"reply_output_{idx}")
                copy_button(result["reply"], label=f"📋 Copy Reply {idx}")

# =========================
# HISTORY PAGE
# =========================
elif page == "History":
    st.markdown("### 🧠 Conversation History")
    if not history_data:
        st.info("No conversation history yet.")
    else:
        clear_col, _ = st.columns([1, 4])
        with clear_col:
            if st.button("Clear History", use_container_width=True):
                save_json(HISTORY_FILE, [])
                st.rerun()

        for i, item in enumerate(reversed(history_data[-50:]), start=1):
            with st.container(border=True):
                st.markdown(f"#### Conversation {i}")
                st.caption(item.get("timestamp", ""))
                render_tags(item.get("tags", []))

                if item.get("needs_human_review"):
                    st.error("🚨 Needs Human Review")
                else:
                    st.success("✅ Normal Review")

                st.write("**Customer:**")
                st.write(item.get("message", ""))
                st.write("**AI Reply:**")
                st.text_area("Reply", item.get("reply", ""), height=180, key=f"history_reply_{i}")

# =========================
# UPGRADE PAGE
# =========================
elif page == "Upgrade":
    st.markdown("### 💎 Upgrade Plan")

    c1, c2 = st.columns(2)

    with c1:
        st.markdown("""
        <div class="price-card">
            <h2>Free</h2>
            <h3>$0</h3>
            <p>For testing your store workflow.</p>
            <ul>
                <li>30 AI generations</li>
                <li>Single + bulk mode</li>
                <li>Product templates</li>
                <li>Human review flags</li>
            </ul>
        </div>
        """, unsafe_allow_html=True)

    with c2:
        st.markdown("""
        <div class="price-card price-card-pro">
            <h2>Pro</h2>
            <h3>$19/month</h3>
            <p>For active sellers who reply to customers daily.</p>
            <ul>
                <li>1,000 AI generations</li>
                <li>Bulk replies</li>
                <li>Conversation history</li>
                <li>Priority support workflow</li>
            </ul>
        </div>
        """, unsafe_allow_html=True)

        if is_pro:
            st.success("You are already on Pro.")

            if st.button("Manage Billing", use_container_width=True):
                try:
                    portal_url = create_billing_portal_url(user_record)
                    st.link_button("Open Stripe Billing Portal", portal_url, use_container_width=True)
                except Exception as e:
                    st.error(str(e))
        else:
            if st.button("Upgrade with Stripe", use_container_width=True, type="primary"):
                try:
                    checkout_url = create_checkout_url(current_user, user_record)
                    st.link_button("Continue to Secure Checkout", checkout_url, use_container_width=True)
                except Exception as e:
                    st.error(str(e))

    st.info(
        "MVP note: this app verifies successful checkout after Stripe redirects back. "
        "For production, use Stripe webhooks as the source of truth."
    )
