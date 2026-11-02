"""
Lab 2 churn features as plain pandas functions, so they can be unit tested.

This is a port of compute_rfm_features() in glue-scripts/feature_engineer.py.
The definitions must stay identical to the Glue job:

  * Only purchases on or before the snapshot date T count (the observation
    window). Anything after T belongs to the holdout and would leak the label.
  * Every window is measured backwards from T, never from today():
    days_ago = T - purchase_date, and "in the last N days" means days_ago < N.
  * avg_basket_size_6m is ITEMS per order over the last 180 days, not dollars.
  * category_diversity_score ignores the 'unknown' category that the
    transform job imputes for missing values.

Input is transaction grain (Lab 2 processed output, one row per purchase).
Output is customer grain (one row per customer_id).
"""

from __future__ import annotations

import pandas as pd

N_CATEGORIES = 8.0           # denominator for category_diversity_score
NO_PURCHASE_SENTINEL = 999   # days_since_last_purchase for a customer with no history


def _history(transactions: pd.DataFrame, snapshot_date) -> pd.DataFrame:
    """Purchases on or before T, with purchase_date as a date and days_ago added."""
    T = pd.Timestamp(snapshot_date).normalize()
    tx = transactions.copy()
    tx["purchase_date"] = pd.to_datetime(tx["purchase_date"]).dt.normalize()
    tx = tx[tx["purchase_date"] <= T]
    tx["days_ago"] = (T - tx["purchase_date"]).dt.days
    return tx


def _with_roster(features: pd.DataFrame, customer_ids, fill: dict) -> pd.DataFrame:
    """Left-join onto a customer roster so customers with no purchases still get a row."""
    if customer_ids is None:
        return features
    roster = customer_ids[["customer_id"]].drop_duplicates()
    out = roster.merge(features, on="customer_id", how="left")
    return out.fillna(fill)


def compute_recency_features(transactions: pd.DataFrame,
                             snapshot_date,
                             customer_ids: pd.DataFrame | None = None,
                             sentinel: int = NO_PURCHASE_SENTINEL) -> pd.DataFrame:
    """days_since_last_purchase and customer_tenure_days, measured from T.

    Pass customer_ids (a DataFrame with a customer_id column) to include
    customers with no purchases. They get days_since_last_purchase = sentinel
    (999 by default: "longer ago than any real customer") and tenure 0.
    """
    T = pd.Timestamp(snapshot_date).normalize()
    tx = _history(transactions, snapshot_date)
    g = tx.groupby("customer_id")["purchase_date"]
    features = pd.DataFrame({
        "days_since_last_purchase": (T - g.max()).dt.days,
        "customer_tenure_days": (T - g.min()).dt.days,
    }).reset_index().astype({"days_since_last_purchase": float, "customer_tenure_days": float})
    return _with_roster(features, customer_ids,
                        {"days_since_last_purchase": float(sentinel), "customer_tenure_days": 0.0})


def compute_frequency_features(transactions: pd.DataFrame, snapshot_date) -> pd.DataFrame:
    """Order counts in the 30, 90 and 180 days before T."""
    tx = _history(transactions, snapshot_date)
    for n in (30, 90, 180):
        tx[f"purchase_frequency_{n}d"] = (tx["days_ago"] < n).astype(float)
    cols = ["purchase_frequency_30d", "purchase_frequency_90d", "purchase_frequency_180d"]
    return tx.groupby("customer_id")[cols].sum().reset_index()


def compute_monetary_features(transactions: pd.DataFrame, snapshot_date) -> pd.DataFrame:
    """avg_order_value, total_spend_90d, total_lifetime_value, avg_basket_size_6m."""
    tx = _history(transactions, snapshot_date)
    in90 = tx["days_ago"] < 90
    in180 = tx["days_ago"] < 180
    tx["_spend_90d"] = tx["order_value"].where(in90, 0.0)
    tx["_items_180d"] = tx["num_items"].where(in180, 0)
    tx["_orders_180d"] = in180.astype(int)

    g = tx.groupby("customer_id").agg(
        avg_order_value=("order_value", "mean"),
        total_spend_90d=("_spend_90d", "sum"),
        total_lifetime_value=("order_value", "sum"),
        _items_180d=("_items_180d", "sum"),
        _orders_180d=("_orders_180d", "sum"),
    )
    # Same guard as the Glue job: no orders in 180 days -> 0 items / max(0, 1) = 0
    g["avg_basket_size_6m"] = g["_items_180d"] / g["_orders_180d"].clip(lower=1)
    cols = ["avg_order_value", "total_spend_90d", "total_lifetime_value", "avg_basket_size_6m"]
    return g[cols].astype(float).reset_index()


def compute_behavioral_features(transactions: pd.DataFrame, snapshot_date=None) -> pd.DataFrame:
    """category_diversity_score and online_to_store_ratio.

    snapshot_date is optional because neither feature has a time window, but
    pass it whenever the data can contain post-T purchases, or they will leak in.
    """
    tx = _history(transactions, snapshot_date) if snapshot_date is not None else transactions.copy()
    known = tx["product_category"].where(tx["product_category"] != "unknown")
    tx["_online"] = (tx["channel"] == "online").astype(float)
    g = tx.assign(_category=known).groupby("customer_id").agg(
        _n_categories=("_category", "nunique"),   # nunique skips the NaN 'unknown's
        online_to_store_ratio=("_online", "mean"),
    )
    g["category_diversity_score"] = g["_n_categories"] / N_CATEGORIES
    cols = ["category_diversity_score", "online_to_store_ratio"]
    return g[cols].astype(float).reset_index()


def build_churn_feature_set(customers: pd.DataFrame,
                            transactions: pd.DataFrame,
                            snapshot_date) -> pd.DataFrame:
    """Every feature, one row per customer in `customers`.

    Null strategy: customers with at least one purchase on or before T have
    every feature defined, so they never get NaN. Customers on the roster with
    no purchases get days_since_last_purchase = 999 (the sentinel) and 0 for
    everything else, since they have no orders, spend or channel history.
    Any other columns on `customers` (loyalty_tier, churn_label) are kept.
    """
    features = compute_recency_features(transactions, snapshot_date)
    for part in (compute_frequency_features(transactions, snapshot_date),
                 compute_monetary_features(transactions, snapshot_date),
                 compute_behavioral_features(transactions, snapshot_date)):
        features = features.merge(part, on="customer_id", how="outer")

    out = customers.merge(features, on="customer_id", how="left")
    feature_cols = [c for c in features.columns if c != "customer_id"]
    fill = {c: 0.0 for c in feature_cols}
    fill["days_since_last_purchase"] = float(NO_PURCHASE_SENTINEL)
    out[feature_cols] = out[feature_cols].fillna(fill)
    return out
