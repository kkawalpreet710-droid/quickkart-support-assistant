import json
import sqlite3

DB_PATH = "quickkart_orders.db"

REFUND_TIMELINE = {
    "wallet": "QuickKart Wallet, instant (within 5 minutes)",
    "UPI": "back to the UPI account in 2 to 3 working days",
    "card": "back to the card in 5 to 7 working days",
    "netbanking": "back to the bank account in 5 to 7 working days",
    "COD": "QuickKart Wallet within 24 hours, because no money was paid online",
}


def open_database():
    """Open the sample orders database read-only (the agent can only read it)."""
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False)


# ---------- tools ----------

def lookup_order(conn, order_id):
    cur = conn.cursor()
    order = cur.execute("""
        SELECT o.order_id, c.name, o.payment_method, o.order_value,
               ROUND((julianday(d.delivered_at) - julianday(d.estimated_at)) * 1440) AS delay_minutes,
               d.weather_delay_mode
        FROM orders o
        JOIN customers c ON c.customer_id = o.customer_id
        JOIN deliveries d ON d.order_id = o.order_id
        WHERE o.order_id = ?
    """, (order_id,)).fetchone()

    if order is None:
        return {"error": f"No order found with id {order_id}"}

    items = cur.execute("""
        SELECT product_name, perishable, quantity, price, delivered
        FROM order_items WHERE order_id = ?
    """, (order_id,)).fetchall()

    return {
        "order_id": order[0],
        "customer": order[1],
        "payment_method": order[2],
        "order_value": order[3],
        "delay_minutes": int(order[4]),
        "weather_delay_mode": bool(order[5]),
        "items": [
            {"product": i[0], "perishable": bool(i[1]), "quantity": i[2],
             "price": i[3], "delivered": bool(i[4])}
            for i in items
        ],
    }


def late_delivery_credit(delay_minutes, weather_delay_mode):
    """Rs. wallet credit owed for a late order, per the Late Delivery Policy."""
    if weather_delay_mode:
        return 0
    elif delay_minutes <= 15:
        return 0
    elif delay_minutes <= 30:
        return 30
    else:
        return 75


def late_credit_tool(delay_minutes, weather_delay_mode):
    credit = late_delivery_credit(delay_minutes, weather_delay_mode)
    if weather_delay_mode:
        note = "Weather Delay Mode was on, so no late-delivery compensation is paid (Late Delivery Policy, clause 3.1)."
    elif credit == 0:
        note = "The delay is within the 15-minute grace period, so no compensation is paid (Late Delivery Policy, clause 2.1)."
    else:
        note = "Credited to QuickKart Wallet automatically within 30 minutes after delivery (Late Delivery Policy, clause 2.5). Compensation credits expire after 90 days."
    return {"credit_rs": credit, "note": note}


def missing_item_refund(conn, order_id):
    order = lookup_order(conn, order_id)
    if "error" in order:
        return order

    missing = [i for i in order["items"] if not i["delivered"]]
    amount = sum(i["price"] * i["quantity"] for i in missing)

    return {
        "order_id": order_id,
        "missing_items": [i["product"] for i in missing],
        "refund_amount": amount,
        "how_and_when": REFUND_TIMELINE[order["payment_method"]],
        "delivery_and_handling_fees_also_refunded": len(missing) == len(order["items"]),
    }


def make_tools(conn, collection):
    """Bundle the tools with the database connection and the policy search index."""

    def search_policy(question, k=5):
        res = collection.query(query_texts=[question], n_results=k)
        return [{"source": m["source"], "text": t}
                for m, t in zip(res["metadatas"][0], res["documents"][0])]

    def lookup(order_id):
        return lookup_order(conn, order_id)

    def missing(order_id):
        return missing_item_refund(conn, order_id)

    return {
        "search_policy": search_policy,
        "lookup_order": lookup,
        "late_delivery_credit": late_credit_tool,
        "missing_item_refund": missing,
    }


# ---------- what the model is told about the tools ----------

TOOLS = [
    {"type": "function", "function": {
        "name": "search_policy",
        "description": "Search the QuickKart policy documents (refunds, late delivery, cancellations, item issues, payments, fees, special cases). Use for any question about rules, timelines, fees, deadlines or conditions. Returns passages with their source file.",
        "parameters": {"type": "object",
                       "properties": {"question": {"type": "string", "description": "What to look up, in plain words"}},
                       "required": ["question"]}}},
    {"type": "function", "function": {
        "name": "lookup_order",
        "description": "Get the facts of one customer order from the orders database: payment method, order value, delay in minutes compared with the estimate, whether Weather Delay Mode was on, and each item with delivered true or false (delivered false means the item was missing). Use whenever the customer mentions an order id.",
        "parameters": {"type": "object",
                       "properties": {"order_id": {"type": "integer"}},
                       "required": ["order_id"]}}},
    {"type": "function", "function": {
        "name": "late_delivery_credit",
        "description": "Calculator: the Rs. wallet credit owed for a late delivered order under the Late Delivery Policy. Always use this instead of working out the amount yourself. Take delay_minutes and weather_delay_mode from lookup_order.",
        "parameters": {"type": "object",
                       "properties": {"delay_minutes": {"type": "integer"},
                                      "weather_delay_mode": {"type": "boolean"}},
                       "required": ["delay_minutes", "weather_delay_mode"]}}},
    {"type": "function", "function": {
        "name": "missing_item_refund",
        "description": "Calculator: the refund amount for items that were not delivered in an order, which items they were, how and when the money comes back, and whether delivery and handling fees are also refunded. Use when an item is missing.",
        "parameters": {"type": "object",
                       "properties": {"order_id": {"type": "integer"}},
                       "required": ["order_id"]}}},
]

SYSTEM = """You are a customer support agent for QuickKart.
- Use lookup_order for facts about a specific order. Use search_policy for rules.
- Never work out credits or refund amounts yourself: use late_delivery_credit and missing_item_refund.
- State only facts that appear in a tool result. Do not invent policy names, timings or conditions. If you need a timing or condition, call search_policy first.
- Delivery and handling fees are refunded only if a tool result says so.
- If a rule differs by case (for example perishable and non-perishable items) and the customer does not say which case applies, give every case.
- If lookup_order returns an error, say you could not find that order id and ask the customer to check the number. Do not use the "I don't know" sentence for this.
- Call search_policy at most 2 times per question.
- Mention which policy file or tool each answer came from.
- If the tools do not give the answer, reply exactly: "I don't know based on the policy documents."
"""


# ---------- the agent loop ----------

def run_agent(question, groq_client, model, conn, collection, max_steps=6, max_searches=2):
    """Returns (answer_text, trace). The trace lists each tool call, for display."""
    tools_by_name = make_tools(conn, collection)
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": question}]
    trace = []
    searches = 0

    for step in range(max_steps):
        last_chance = (step == max_steps - 1)
        response = groq_client.chat.completions.create(
            model=model, messages=messages, tools=TOOLS,
            tool_choice="none" if last_chance else "auto", temperature=0)
        msg = response.choices[0].message

        if not msg.tool_calls:
            if msg.content and msg.content.strip():
                return msg.content, trace
            messages.append({"role": "user", "content":
                "Please write your final answer to the customer now, using the tool results above."})
            continue

        messages.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [{"id": tc.id, "type": "function",
                                         "function": {"name": tc.function.name,
                                                      "arguments": tc.function.arguments}}
                                        for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            name = tc.function.name
            args = json.loads(tc.function.arguments or "{}")
            trace.append(f"{name}({args})")

            if name == "search_policy":
                searches += 1
            if name == "search_policy" and searches > max_searches:
                result = {"error": "Search limit reached. Do not search again. Answer using what you already have, or reply exactly: I don't know based on the policy documents."}
                trace.append("(search limit reached)")
            else:
                try:
                    result = tools_by_name[name](**args)
                except Exception as e:
                    result = {"error": str(e)}

            if name == "lookup_order" and isinstance(result, dict) and "error" in result:
                return (f"I couldn't find order {args.get('order_id')}. "
                        "Could you double-check the order number?"), trace

            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result)})

    return "Stopped: too many steps.", trace
