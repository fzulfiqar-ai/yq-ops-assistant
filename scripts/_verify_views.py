"""Quick data quality check against live Supabase views."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from app.database import get_client

client = get_client()

# 1) Revenue fields in v_sales
r = client.table("v_sales").select("customer_name,gross_bhd,total_amount_bhd").limit(5).execute()
print("v_sales sample amounts:")
for row in r.data:
    print(f"  {str(row['customer_name'])[:28]:28}  gross={row['gross_bhd']}  total={row['total_amount_bhd']}")

# 2) Top customers by revenue
r2 = client.table("v_top_customers").select("customer_name,total_revenue_bhd,order_count").limit(5).execute()
print("\nv_top_customers (top 5):")
for row in r2.data:
    print(f"  {str(row['customer_name'])[:30]:30}  revenue={row['total_revenue_bhd']}  orders={row['order_count']}")

# 3) Monthly sales
r3 = client.table("v_sales_by_period").select("period_month,net_revenue_bhd,order_count").execute()
print("\nv_sales_by_period:")
for row in r3.data:
    print(f"  {row['period_month']}  revenue={row['net_revenue_bhd']}  orders={row['order_count']}")
