import json
import os
import streamlit as st
from openai import OpenAI
if "reply" not in st.session_state:
    st.session_state.reply = ""
st.set_page_config(
    page_title="AI Customer Reply Assistant",
    page_icon="🤖",
    layout="wide"
)

client = OpenAI(api_key="YOUR_NEW_API_KEY")

st.markdown("## 🤖 AI Customer Reply Assistant")
st.caption("Smart replies for e-commerce sellers")

col1, col2 = st.columns([1, 2])

# -------------------------
# Load config
# -------------------------
config_file = "config.json"

if os.path.exists(config_file):
    with open(config_file, "r") as f:
        config = json.load(f)
else:
    config = {"products": []}

# =========================
# LEFT SIDE (Product)
# =========================
st.markdown("")
with col1:
    st.markdown("### 📦 Product Setup")

    st.subheader("Add New Product")

    new_name = st.text_input("Product Name")
    new_shipping = st.text_input("Shipping Time")
    new_returns = st.text_input("Return Policy")

    if st.button("Add Product"):
        if new_name:
            config["products"].append({
                "name": new_name,
                "shipping": new_shipping,
                "returns": new_returns
            })

            with open(config_file, "w") as f:
                json.dump(config, f)

            st.success("Product added!")
        else:
            st.warning("Please enter a product name.")
    st.divider()           
    st.subheader("Select Product")

    if len(config["products"]) == 0:
        st.warning("Please add at least one product first.")
        st.stop()

    product_names = [p["name"] for p in config["products"]]

    selected_name = st.selectbox("Choose a product", product_names)

    selected_product = next(
        (p for p in config["products"] if p["name"] == selected_name),
        None
    )

    st.subheader("Manage Products")

    for i, product in enumerate(config["products"]):
        with st.expander(product["name"]):

            edit_name = st.text_input("Name", value=product["name"], key=f"name_{i}")
            edit_shipping = st.text_input("Shipping", value=product["shipping"], key=f"ship_{i}")
            edit_returns = st.text_input("Returns", value=product["returns"], key=f"ret_{i}")

            col_edit1, col_edit2 = st.columns(2)

            with col_edit1:
                if st.button("Save", key=f"save_{i}"):
                    config["products"][i] = {
                        "name": edit_name,
                        "shipping": edit_shipping,
                        "returns": edit_returns
                    }

                    with open(config_file, "w") as f:
                        json.dump(config, f)

                    st.success("Updated!")

            with col_edit2:
                if st.button("Delete", key=f"delete_{i}"):
                    config["products"].pop(i)

                    with open(config_file, "w") as f:
                        json.dump(config, f)

                    st.rerun()

# =========================
# RIGHT SIDE (Customer)
# =========================
st.markdown("")
with col2:
    st.markdown("### 💬 Customer Message")

    customer_message = st.text_area(
        "Paste message",
        height=150,
        placeholder="e.g. My package arrived damaged..."
    )

    if st.button("✨ Generate Reply", use_container_width=True, type="primary"):

        prompt = f"""
        You are a professional e-commerce seller.

        Product: {selected_product["name"]}
        Shipping: {selected_product["shipping"]}
        Return policy: {selected_product["returns"]}

        Rules:
        - Friendly and human tone
        - Apologize if needed
        - Provide clear solution
        - Keep under 80 words

        Customer message:
        {customer_message}
        """

        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}]
        )

        st.session_state.reply = response.choices[0].message.content

st.divider()

st.subheader("📝 Suggested Reply")

if st.session_state.reply:
    st.divider()

    st.subheader("📝 Suggested Reply")

    st.text_area("Reply", st.session_state.reply, height=200)

    st.success("✔ Ready to copy and send")