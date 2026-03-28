# SQL Dependency Analysis

## Summary
- **Total source tables**: 5
- **Total columns referenced**: 47 unique columns across all tables
- **Query type**: Complex aggregation with multiple CTEs, self-joins, and multi-table joins
- **Target table**: `ex_offline.dws_okx_risk_deposit_withdraw_aggr_df`

---

## Source Table Dependencies

### 1. ex_offline.dwd_okx_depositwithdraw_onchain_order_core_df

**Usage Pattern**: Self-join scenario (used twice with different filters)
- **First use**: Withdrawal transactions (`withdraw_base` CTE)
- **Second use**: Deposit transactions (`deposit_base` CTE)

**Columns used:**
- `id` - order identifier (SELECT, aggregations)
- `user_id` - user identifier (SELECT, JOIN key, GROUP BY)
- `ip` - IP address, transformed as `LOWER(CAST(ip AS STRING))`
- `device_id` - device identifier, transformed as `LOWER(CAST(device_id AS STRING))`
- `address` - blockchain address (withdrawal: raw; deposit: `LOWER(CAST(...))`)
- `total_volume_token` - transaction volume in token units (SELECT, aggregations)
- `volume_usdt` - transaction volume in USDT (SELECT, aggregations, calculations)
- `currency_id` - currency identifier (SELECT, DISTINCT counts)
- `status` - transaction status (SELECT, CASE conditions for filtering)
- `transaction_hash` - blockchain transaction hash (SELECT, DISTINCT counts)
- `from_address` - source address (SELECT, DISTINCT counts)
- `create_time` - transaction timestamp (SELECT, date calculations, JOIN conditions)
- `pt` - partition column (WHERE filter)
- `business_type` - transaction type: 1=deposit, 2=withdrawal (WHERE filter)

**Filters applied (withdrawal):**
```sql
pt = '${bdp.system.bizdate}'
AND business_type = 2
```

**Filters applied (deposit):**
```sql
pt = '${bdp.system.bizdate}'
AND business_type = 1
```

**Sample data query (withdrawals):**
```sql
-- Sample withdrawal transactions
-- Replace ${bdp.system.bizdate} with actual date (e.g., '20260323')
SELECT
    id,
    user_id,
    LOWER(CAST(ip AS STRING)) AS ip,
    LOWER(CAST(device_id AS STRING)) AS device_id,
    address,
    total_volume_token,
    volume_usdt,
    currency_id,
    status,
    transaction_hash,
    from_address,
    create_time,
    pt,
    business_type
FROM ex_offline.dwd_okx_depositwithdraw_onchain_order_core_df
WHERE pt = '${bdp.system.bizdate}'
  AND business_type = 2
LIMIT 1000;
```

**Sample data query (deposits):**
```sql
-- Sample deposit transactions
-- Replace ${bdp.system.bizdate} with actual date (e.g., '20260323')
SELECT
    id,
    user_id,
    LOWER(CAST(ip AS STRING)) AS ip,
    LOWER(CAST(device_id AS STRING)) AS device_id,
    LOWER(CAST(address AS STRING)) AS address,
    total_volume_token,
    volume_usdt,
    currency_id,
    status,
    transaction_hash,
    from_address,
    create_time,
    pt,
    business_type
FROM ex_offline.dwd_okx_depositwithdraw_onchain_order_core_df
WHERE pt = '${bdp.system.bizdate}'
  AND business_type = 1
LIMIT 1000;
```

---

### 2. ex_offline.ods_okx_payment_cash_deposit_order_core_df

**Usage Pattern**: Cash deposit orders with currency conversion

**Columns used:**
- `public_order_id` - unique order identifier (SELECT, COUNT aggregations)
- `create_on` - order creation timestamp (SELECT, DATEDIFF calculations)
- `user_id` - user identifier (SELECT, GROUP BY)
- `amount` - transaction amount in original currency (SELECT, aggregations, calculations)
- `status` - order status (CASE condition: values 3,1,10,11 = approved)
- `symbol` - currency symbol (JOIN key with rate table)
- `channel_id` - payment channel identifier (WHERE exclusion filter)
- `pt` - partition column (WHERE filter)

**Filters applied:**
```sql
pt = '${bdp.system.bizdate}'
AND channel_id NOT IN (51)
```

**Sample data query:**
```sql
-- Sample cash deposit orders (excluding channel 51)
-- Replace ${bdp.system.bizdate} with actual date (e.g., '20260323')
SELECT
    public_order_id,
    create_on,
    user_id,
    amount,
    status,
    symbol,
    channel_id,
    pt
FROM ex_offline.ods_okx_payment_cash_deposit_order_core_df
WHERE pt = '${bdp.system.bizdate}'
  AND channel_id NOT IN (51)
LIMIT 1000;
```

---

### 3. ex_offline.ko_ods_market_rate

**Usage Pattern**: Currency exchange rate lookup (dimension table)

**Columns used:**
- `rate_name` - currency pair name, transformed as `TOUPPER(REPLACE(rate_name,'usd_',''))` to extract symbol
- `rate_parities` - exchange rate value, inverted as `1/rate_parities` to get USD conversion price
- `pt` - partition column (WHERE filter)

**Filters applied:**
```sql
pt = '${bdp.system.bizdate}'
```

**Sample data query:**
```sql
-- Sample currency exchange rates with transformation
-- Replace ${bdp.system.bizdate} with actual date (e.g., '20260323')
SELECT
    rate_name,
    TOUPPER(REPLACE(rate_name,'usd_','')) AS symbol,
    rate_parities,
    1/rate_parities AS price,
    pt
FROM ex_offline.ko_ods_market_rate
WHERE pt = '${bdp.system.bizdate}'
LIMIT 1000;
```

---

### 4. ex_offline.dwd_okx_depositwithdraw_p2p_order_core_df

**Usage Pattern**: P2P transfer orders (used twice with different user roles)
- **First use**: Buyer deposits (`p2p_deposit_txn` CTE)
- **Second use**: Seller withdrawals (`p2p_withdraw_txn` CTE)

**Columns used:**
- `buyer_user_id` - buyer user identifier (aliased as `user_id` in deposit query)
- `seller_user_id` - seller user identifier (aliased as `user_id` in withdrawal query)
- `buyer_in_volume_usdt` - buyer received amount in USDT (SUM aggregation)
- `buyer_in_volume_token` - buyer received amount in token (SUM aggregation)
- `seller_out_volume_usdt` - seller sent amount in USDT (SUM aggregation)
- `seller_out_volume_token` - seller sent amount in token (SUM aggregation)
- `create_time` - order creation timestamp (DATEDIFF calculation)
- `order_status` - order status (WHERE filter: 4 = completed)
- `pt` - partition column (WHERE filter)

**Filters applied (P2P deposits):**
```sql
pt = '${bdp.system.bizdate}'
AND DATEDIFF(DATE(TO_DATE('${bdp.system.bizdate}', 'yyyymmdd')), DATE(create_time), 'dd') <= 0
AND order_status = 4
```

**Filters applied (P2P withdrawals):**
```sql
pt = '${bdp.system.bizdate}'
AND DATEDIFF(DATE(TO_DATE('${bdp.system.bizdate}', 'yyyymmdd')), DATE(create_time), 'dd') <= 0
AND order_status = 4
```

**Sample data query (P2P deposits - buyer side):**
```sql
-- Sample P2P deposit transactions (buyer received)
-- Replace ${bdp.system.bizdate} with actual date (e.g., '20260323')
SELECT
    buyer_user_id,
    buyer_in_volume_usdt,
    buyer_in_volume_token,
    create_time,
    order_status,
    pt
FROM ex_offline.dwd_okx_depositwithdraw_p2p_order_core_df
WHERE pt = '${bdp.system.bizdate}'
  AND DATEDIFF(DATE(TO_DATE('${bdp.system.bizdate}', 'yyyymmdd')), DATE(create_time), 'dd') <= 0
  AND order_status = 4
LIMIT 1000;
```

**Sample data query (P2P withdrawals - seller side):**
```sql
-- Sample P2P withdrawal transactions (seller sent)
-- Replace ${bdp.system.bizdate} with actual date (e.g., '20260323')
SELECT
    seller_user_id,
    seller_out_volume_usdt,
    seller_out_volume_token,
    create_time,
    order_status,
    pt
FROM ex_offline.dwd_okx_depositwithdraw_p2p_order_core_df
WHERE pt = '${bdp.system.bizdate}'
  AND DATEDIFF(DATE(TO_DATE('${bdp.system.bizdate}', 'yyyymmdd')), DATE(create_time), 'dd') <= 0
  AND order_status = 4
LIMIT 1000;
```

---

### 5. ex_offline.dwd_okx_depositwithdraw_fiatgateway_order_core_df

**Usage Pattern**: Fiat gateway crypto purchase orders

**Columns used:**
- `user_id` - user identifier (SELECT, GROUP BY)
- `volume_usdt` - purchase amount in USDT (SUM aggregation)
- `volume_token` - purchase amount in token (SUM aggregation)
- `create_time` - order creation timestamp (DATEDIFF calculation)
- `status` - order status (WHERE filter: 3 = completed)
- `pt` - partition column (WHERE filter)

**Filters applied:**
```sql
pt = '${bdp.system.bizdate}'
AND DATEDIFF(DATE(TO_DATE('${bdp.system.bizdate}', 'yyyymmdd')), DATE(create_time), 'dd') <= 0
AND status = 3
```

**Sample data query:**
```sql
-- Sample fiat gateway crypto purchase orders
-- Replace ${bdp.system.bizdate} with actual date (e.g., '20260323')
SELECT
    user_id,
    volume_usdt,
    volume_token,
    create_time,
    status,
    pt
FROM ex_offline.dwd_okx_depositwithdraw_fiatgateway_order_core_df
WHERE pt = '${bdp.system.bizdate}'
  AND DATEDIFF(DATE(TO_DATE('${bdp.system.bizdate}', 'yyyymmdd')), DATE(create_time), 'dd') <= 0
  AND status = 3
LIMIT 1000;
```

---

### 6. ex_offline.dim_okx_user_master_id_relation_df

**Usage Pattern**: User master ID mapping (dimension table for JOIN)

**Columns used:**
- `user_id` - user identifier (JOIN key)
- `master_user_id` - master user identifier (SELECT)
- `pt` - partition column (JOIN condition)

**Filters applied:**
```sql
pt = '${bdp.system.bizdate}'
```

**Sample data query:**
```sql
-- Sample user master ID mappings
-- Replace ${bdp.system.bizdate} with actual date (e.g., '20260323')
SELECT
    user_id,
    master_user_id,
    pt
FROM ex_offline.dim_okx_user_master_id_relation_df
WHERE pt = '${bdp.system.bizdate}'
LIMIT 1000;
```

---

## Key Business Logic Notes

### Status Code Mappings

1. **Onchain orders** (`dwd_okx_depositwithdraw_onchain_order_core_df`):
   - `status = 2` → Successful/completed transactions

2. **Cash deposit orders** (`ods_okx_payment_cash_deposit_order_core_df`):
   - `status IN (3,1,10,11)` → Risk-approved transactions

3. **P2P orders** (`dwd_okx_depositwithdraw_p2p_order_core_df`):
   - `order_status = 4` → Completed orders

4. **Fiat gateway orders** (`dwd_okx_depositwithdraw_fiatgateway_order_core_df`):
   - `status = 3` → Completed purchases

### Date Window Calculations

All queries use `${bdp.system.bizdate}` as the reference date for:
- Partition filtering
- Days-since calculations (e.g., `days_since_first_crypto_withdrawal`)
- Time window filters (e.g., last 1 day, last 7 days, last 30 days)

### Important Parameter

**`${bdp.system.bizdate}`**: MaxCompute parameter placeholder for business date
- Format: `yyyymmdd` (e.g., `20260323`)
- Used across all partition filters and date calculations
- Must be replaced with actual date when running sample queries

---

## Query Architecture

This SQL implements a **feature engineering pipeline** for risk analysis, combining:
- Crypto deposit/withdrawal behavior metrics
- Cash transaction statistics
- P2P transfer patterns
- Fiat gateway purchase activity
- Temporal patterns (withdrawals after deposits within 24h/48h/72h windows)

The final output contains **60+ risk features per user**, aggregated from multiple transaction sources.
