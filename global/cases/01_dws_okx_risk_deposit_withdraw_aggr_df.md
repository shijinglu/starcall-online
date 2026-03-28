# Making `dws_okx_risk_deposit_withdraw_aggr_df` realtime 

## Original SQL

```sql
SET odps.mcqa.disable=true;
SET odps.task.wlm.quota=ex_highlevel_quota;
-- PRD https://okg-block.sg.larksuite.com/wiki/LJnWwHwymiaM5QkIarklfKQdgzd
SET odps.sql.type.system.odps2=true;
SET odps.sql.decimal.odps2=true;
set odps.sql.type.system.odps2=true;

with withdraw_base as (
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
        DATEDIFF(date(to_date('${bdp.system.bizdate}', 'yyyymmdd')),date(create_time), 'dd') as days_window,
        create_time
    FROM ex_offline.dwd_okx_depositwithdraw_onchain_order_core_df
    WHERE pt = '${bdp.system.bizdate}' and business_type = 2
), 

withdraw_time_stats AS (
    SELECT
        user_id,
        MIN(CASE WHEN status = 2 THEN create_time END) AS first_withdrawal_time,
        MAX(CASE WHEN status = 2 THEN create_time END) AS last_withdrawal_time
    FROM withdraw_base
    GROUP BY user_id
),

withdraw_freq_stats AS (
    SELECT
        w.user_id AS withdraw_freq_stats_user_id,
        COALESCE(SUM(CASE WHEN days_window <= 0 THEN 1 ELSE 0 END), 0) AS crypto_withdrawal_cnt_last_1d,
        COALESCE(SUM(CASE WHEN days_window <= 0 THEN COALESCE(total_volume_token,0) ELSE 0 END), 0) AS crypto_withdrawal_volume_token_last_1d,
        COALESCE(SUM(CASE WHEN days_window <= 0 THEN COALESCE(volume_usdt,0) ELSE 0 END), 0) AS crypto_withdrawal_volume_usdt_last_1d,
        --------ads_okx_risk_ato_offline_feature_df
        DATEDIFF(DATE(TO_DATE('${bdp.system.bizdate}','yyyymmdd')), MIN(CASE WHEN status = 2 THEN DATE(create_time) END), 'dd') AS days_since_first_crypto_withdrawal,
        DATEDIFF(DATE(TO_DATE('${bdp.system.bizdate}','yyyymmdd')), MAX(CASE WHEN status = 2 THEN DATE(create_time) END), 'dd') AS days_since_last_crypto_withdrawal,
        MIN(CASE WHEN status = 2 THEN create_time END) AS first_withdrawal_date,
        MAX(CASE WHEN status = 2 THEN create_time END) AS last_withdrawal_date,
        COUNT(DISTINCT CASE WHEN status = 2 AND days_window < 1 THEN transaction_hash END) AS crypto_withdrawal_success_count_last_1d,
        ROUND(SUM(CASE WHEN status = 2 AND days_window < 1 THEN volume_usdt ELSE 0 END),2) AS crypto_withdrawal_success_amount_last_1d,
        COUNT(DISTINCT CASE WHEN status = 2 AND days_window < 1 THEN currency_id END) AS crypto_withdrawal_currency_type_count_last_1d,
        -----ads_okx_risk_user_offline_feature_crypto_withdraw_df
        COUNT(DISTINCT id) AS crypto_withdraw_order_total_cnt,
        ROUND(SUM(COALESCE(volume_usdt,0)),2) AS crypto_withdraw_usdt_total_amount,
        COUNT(DISTINCT address) AS crypto_withdraw_address_total_cnt,
        ---ads_okx_risk_ccs_offline_features_df
        SUM(CASE WHEN w.create_time = t.first_withdrawal_time THEN w.volume_usdt ELSE 0 END) AS first_withdrawal_volume_usdt,
        SUM(CASE WHEN w.create_time = t.last_withdrawal_time THEN w.volume_usdt ELSE 0 END) AS last_withdrawal_volume_usdt
        --other metrics
    FROM withdraw_base w
    JOIN withdraw_time_stats t ON w.user_id = t.user_id
    GROUP BY w.user_id
),

deposit_base as (
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
        DATEDIFF(date(to_date('${bdp.system.bizdate}', 'yyyymmdd')),date(create_time), 'dd') as days_window,
        create_time
    FROM ex_offline.dwd_okx_depositwithdraw_onchain_order_core_df
    WHERE pt = '${bdp.system.bizdate}' and business_type = 1
),

deposit_time_stats AS (
    SELECT
        user_id,
        MIN(CASE WHEN status = 2 THEN create_time END) AS first_deposit_time,
        MAX(CASE WHEN status = 2 THEN create_time END) AS last_deposit_time
    FROM deposit_base
    GROUP BY user_id
),

deposit_freq_stats AS (
    SELECT
        d.user_id AS deposit_freq_stats_user_id,
        COALESCE(SUM(CASE WHEN days_window <= 0 THEN 1 ELSE 0 END), 0) AS crypto_deposit_cnt_last_1d,
        COALESCE(SUM(CASE WHEN days_window <= 0 THEN COALESCE(total_volume_token,0) ELSE 0 END), 0) AS crypto_deposit_volume_token_last_1d,
        COALESCE(SUM(CASE WHEN days_window <= 0 THEN COALESCE(volume_usdt,0) ELSE 0 END), 0) AS crypto_deposit_volume_usdt_last_1d,
        --------ads_okx_risk_ato_offline_feature_df
        DATEDIFF(DATE(TO_DATE('${bdp.system.bizdate}','yyyymmdd')), MIN(CASE WHEN status = 2 THEN DATE(create_time) END), 'dd') AS days_since_first_crypto_deposit,
        DATEDIFF(DATE(TO_DATE('${bdp.system.bizdate}','yyyymmdd')), MAX(CASE WHEN status = 2 THEN DATE(create_time) END), 'dd') AS days_since_last_crypto_deposit,
        MIN(CASE WHEN status = 2 THEN create_time END) AS first_deposit_date,
        MAX(CASE WHEN status = 2 THEN create_time END) AS last_deposit_date,
        COUNT(DISTINCT CASE WHEN status = 2 AND days_window < 1 THEN transaction_hash END) AS crypto_deposit_success_count_last_1d,
        ROUND(SUM(CASE WHEN status = 2 AND days_window < 1 THEN volume_usdt ELSE 0 END),2) AS crypto_deposit_success_amount_last_1d,
        COUNT(DISTINCT CASE WHEN status = 2 AND days_window < 1 THEN currency_id END) AS crypto_deposit_currency_type_count_last_1d,
        COUNT(DISTINCT CASE WHEN status = 2 AND days_window < 1 THEN from_address END) AS crypto_deposit_success_address_count_last_1d,
        -----ads_okx_risk_user_offline_feature_crypto_deposit_df
        COUNT(DISTINCT id) AS crypto_deposit_order_total_cnt,
        ROUND(SUM(COALESCE(volume_usdt,0)),2) AS crypto_deposit_usdt_total_amount,
        COUNT(DISTINCT address) AS crypto_deposit_address_total_cnt,
        ---ads_okx_risk_ccs_offline_features_df
        SUM(CASE WHEN d.create_time = t.first_deposit_time THEN d.volume_usdt ELSE 0 END) AS first_deposit_volume_usdt,
        SUM(CASE WHEN d.create_time = t.last_deposit_time THEN d.volume_usdt ELSE 0 END) AS last_deposit_volume_usdt
    FROM deposit_base d
    JOIN deposit_time_stats t ON d.user_id = t.user_id
    GROUP BY d.user_id
),

joined_events AS ( 
    SELECT
        w.user_id,
        w.create_time as withdraw_time,
        d.create_time as deposit_time,
        (TO_UNIX_TIMESTAMP(w.create_time) - TO_UNIX_TIMESTAMP(d.create_time)) / 3600 AS hours_after_deposit
    FROM withdraw_base w
    JOIN deposit_base d
        ON w.user_id = d.user_id
    WHERE TO_UNIX_TIMESTAMP(w.create_time) >= TO_UNIX_TIMESTAMP(d.create_time)
),

withdrawal_after_deposit_cnt AS (
    SELECT
    user_id,
    COUNT(CASE WHEN hours_after_deposit <= 24 THEN 1 END) AS withdrawal_24h_after_deposit_cnt_90d,
    COUNT(CASE WHEN hours_after_deposit <= 48 THEN  1 END) AS withdrawal_48h_after_deposit_cnt_90d,
    COUNT(CASE WHEN hours_after_deposit <= 72 THEN 1 END) AS withdrawal_72h_after_deposit_cnt_90d,
    COUNT(CASE WHEN hours_after_deposit <= 24 AND DATEDIFF(date(to_date('${bdp.system.bizdate}', 'yyyymmdd')),date(withdraw_time), 'dd') < 30 THEN 1 END) AS withdrawal_24h_after_deposit_cnt_30d,
    COUNT(CASE WHEN hours_after_deposit <= 48 AND DATEDIFF(date(to_date('${bdp.system.bizdate}', 'yyyymmdd')),date(withdraw_time), 'dd') < 30 THEN 1 END) AS withdrawal_48h_after_deposit_cnt_30d,
    COUNT(CASE WHEN hours_after_deposit <= 72 AND DATEDIFF(date(to_date('${bdp.system.bizdate}', 'yyyymmdd')),date(withdraw_time), 'dd') < 30 THEN 1 END) AS withdrawal_72h_after_deposit_cnt_30d,
    COUNT(CASE WHEN hours_after_deposit <= 24 AND DATEDIFF(date(to_date('${bdp.system.bizdate}', 'yyyymmdd')),date(withdraw_time), 'dd') < 7 THEN 1 END) AS withdrawal_24h_after_deposit_cnt_7d,
    COUNT(CASE WHEN hours_after_deposit <= 48 AND DATEDIFF(date(to_date('${bdp.system.bizdate}', 'yyyymmdd')),date(withdraw_time), 'dd') < 7 THEN 1 END) AS withdrawal_48h_after_deposit_cnt_7d,
    COUNT(CASE WHEN hours_after_deposit <= 72 AND DATEDIFF(date(to_date('${bdp.system.bizdate}', 'yyyymmdd')),date(withdraw_time), 'dd') < 7 THEN 1 END) AS withdrawal_72h_after_deposit_cnt_7d
    FROM joined_events
    GROUP BY user_id
),

transaction_base as (
    SELECT  a.public_order_id
        ,a.create_on
        ,a.user_id
        ,a.amount
        ,coalesce(a.amount * coalesce(currency.price,1),a.amount) as  amount_usd
        ,case when a.status in (3,1,10,11) then 1 else 0 end as risk_approve_ind
    FROM ex_offline.ods_okx_payment_cash_deposit_order_core_df a
    left join (
        select toupper(replace(rate_name,'usd_','')) as symbol,
            1/rate_parities as price
        from ex_offline.ko_ods_market_rate
        where pt = '${bdp.system.bizdate}') currency
            on a.symbol = currency.symbol
    WHERE pt = '${bdp.system.bizdate}'
        and channel_id not in (51)
),
transaction_stats  as (
    select user_id,
        MIN(amount) AS min_txn_amount,
        MAX(amount) AS max_txn_amount,
        COALESCE(COUNT(CASE WHEN CAST(DATEDIFF(to_date('${bdp.system.bizdate}','yyyymmdd'),create_on,'hh') AS BIGINT) <= 24 THEN public_order_id ELSE NULL END),0) AS txn_cnt_last_1d,
        COALESCE(SUM(CASE WHEN CAST(DATEDIFF(to_date('${bdp.system.bizdate}','yyyymmdd'),create_on,'hh') AS BIGINT) <= 24 THEN amount ELSE 0 END),0) AS txn_amt_last_1d,
        round(COALESCE(SUM(CASE WHEN CAST(DATEDIFF(to_date('${bdp.system.bizdate}','yyyymmdd'),create_on,'hh') AS BIGINT) <= 24 THEN amount_usd ELSE 0 END),0), 2) AS txn_amt_usd_last_1d
    from transaction_base
    where risk_approve_ind = 1
    group by user_id
),

p2p_deposit_txn as (
    select 
        buyer_user_id as user_id
        ,SUM(buyer_in_volume_usdt) as p2p_deposit_txn_amt_usdt_last_1d
        ,SUM(buyer_in_volume_token) as p2p_deposit_txn_amt_token_last_1d
    from ex_offline.dwd_okx_depositwithdraw_p2p_order_core_df
    where
        pt = '${bdp.system.bizdate}'
        and DATEDIFF(date(to_date('${bdp.system.bizdate}', 'yyyymmdd')),date(create_time), 'dd') <= 0
        and order_status = 4
    group by buyer_user_id
),
p2p_withdraw_txn as (
    select 
        seller_user_id as user_id
        ,SUM(seller_out_volume_usdt) as p2p_withdraw_txn_amt_usdt_last_1d
        ,SUM(seller_out_volume_token) as p2p_withdraw_txn_amt_token_last_1d
    from ex_offline.dwd_okx_depositwithdraw_p2p_order_core_df
    where 
        pt = '${bdp.system.bizdate}'
        and DATEDIFF(date(to_date('${bdp.system.bizdate}', 'yyyymmdd')),date(create_time), 'dd') <= 0
        and order_status = 4
    group by seller_user_id
),
buy_crypto_txn as (
    select 
        user_id
        ,SUM(volume_usdt) as buy_crypto_txn_amt_usdt_last_1d
        ,SUM(volume_token) as buy_crypto_txn_amt_token_last_1d
    from ex_offline.dwd_okx_depositwithdraw_fiatgateway_order_core_df
    where
        pt = '${bdp.system.bizdate}'
        and DATEDIFF(date(to_date('${bdp.system.bizdate}', 'yyyymmdd')),date(create_time), 'dd') <= 0
        and status = 3
    group by user_id
),
_users AS (
  SELECT deposit_freq_stats_user_id AS user_id from deposit_freq_stats
    UNION 
  SELECT withdraw_freq_stats_user_id AS user_id from withdraw_freq_stats
    UNION
  SELECT user_id from transaction_stats
    UNION 
  SELECT user_id from p2p_deposit_txn
    UNION 
  SELECT user_id from p2p_withdraw_txn
    UNION 
  SELECT user_id from buy_crypto_txn
),
all_users AS (
    SELECT DISTINCT
        u.user_id,
        m.master_user_id
    FROM _users u 
    LEFT JOIN ex_offline.dim_okx_user_master_id_relation_df m
    ON u.user_id = m.user_id
    AND m.pt = '${bdp.system.bizdate}'
)

-- INSERT OVERWRITE TABLE ex_offline.dws_okx_risk_deposit_withdraw_aggr_df PARTITION(pt = '${bdp.system.bizdate}' )
SELECT 
    u.user_id,  
    u.master_user_id,
    COALESCE(d.crypto_deposit_cnt_last_1d, 0) AS crypto_deposit_cnt_last_1d,
    COALESCE(d.crypto_deposit_volume_token_last_1d, 0) AS crypto_deposit_volume_token_last_1d,
    COALESCE(d.crypto_deposit_volume_usdt_last_1d, 0) AS crypto_deposit_volume_usdt_last_1d,
    d.days_since_first_crypto_deposit AS days_since_first_crypto_deposit,
    d.days_since_last_crypto_deposit AS days_since_last_crypto_deposit,
    d.first_deposit_date AS first_deposit_date,
    d.last_deposit_date AS last_deposit_date,
    COALESCE(d.crypto_deposit_success_count_last_1d, 0) AS crypto_deposit_success_count_last_1d,
    COALESCE(d.crypto_deposit_success_amount_last_1d, 0) AS crypto_deposit_success_amount_last_1d,
    COALESCE(d.crypto_deposit_currency_type_count_last_1d, 0) AS crypto_deposit_currency_type_count_last_1d,
    COALESCE(d.crypto_deposit_success_address_count_last_1d, 0) AS crypto_deposit_success_address_count_last_1d,
    COALESCE(d.crypto_deposit_order_total_cnt, 0) AS crypto_deposit_order_total_cnt,
    COALESCE(d.crypto_deposit_usdt_total_amount, 0) AS crypto_deposit_usdt_total_amount,
    COALESCE(d.crypto_deposit_address_total_cnt, 0) AS crypto_deposit_address_total_cnt,
    d.first_deposit_volume_usdt AS first_deposit_volume_usdt,
    d.last_deposit_volume_usdt AS last_deposit_volume_usdt,

    COALESCE(w.crypto_withdrawal_cnt_last_1d, 0) AS crypto_withdrawal_cnt_last_1d,
    COALESCE(w.crypto_withdrawal_volume_token_last_1d, 0) AS crypto_withdrawal_volume_token_last_1d,
    COALESCE(w.crypto_withdrawal_volume_usdt_last_1d, 0) AS crypto_withdrawal_volume_usdt_last_1d,
    w.days_since_first_crypto_withdrawal AS days_since_first_crypto_withdrawal,
    w.days_since_last_crypto_withdrawal AS days_since_last_crypto_withdrawal,
    w.first_withdrawal_date AS first_withdrawal_date,
    w.last_withdrawal_date AS last_withdrawal_date,
    COALESCE(w.crypto_withdrawal_success_count_last_1d, 0) AS crypto_withdrawal_success_count_last_1d,
    COALESCE(w.crypto_withdrawal_success_amount_last_1d, 0) AS crypto_withdrawal_success_amount_last_1d,
    COALESCE(w.crypto_withdrawal_currency_type_count_last_1d, 0) AS crypto_withdrawal_currency_type_count_last_1d,
    COALESCE(w.crypto_withdraw_order_total_cnt, 0) AS crypto_withdraw_order_total_cnt,
    COALESCE(w.crypto_withdraw_usdt_total_amount, 0) AS crypto_withdraw_usdt_total_amount,
    COALESCE(w.crypto_withdraw_address_total_cnt, 0) AS crypto_withdraw_address_total_cnt,
    w.first_withdrawal_volume_usdt AS first_withdrawal_volume_usdt,
    w.last_withdrawal_volume_usdt AS last_withdrawal_volume_usdt,

    COALESCE(wad.withdrawal_24h_after_deposit_cnt_90d, 0) AS withdrawal_24h_after_deposit_cnt_90d,
    COALESCE(wad.withdrawal_48h_after_deposit_cnt_90d, 0) AS withdrawal_48h_after_deposit_cnt_90d,
    COALESCE(wad.withdrawal_72h_after_deposit_cnt_90d, 0) AS withdrawal_72h_after_deposit_cnt_90d,
    COALESCE(wad.withdrawal_24h_after_deposit_cnt_30d, 0) AS withdrawal_24h_after_deposit_cnt_30d,
    COALESCE(wad.withdrawal_48h_after_deposit_cnt_30d, 0) AS withdrawal_48h_after_deposit_cnt_30d,
    COALESCE(wad.withdrawal_72h_after_deposit_cnt_30d, 0) AS withdrawal_72h_after_deposit_cnt_30d,
    COALESCE(wad.withdrawal_24h_after_deposit_cnt_7d, 0) AS withdrawal_24h_after_deposit_cnt_7d,
    COALESCE(wad.withdrawal_48h_after_deposit_cnt_7d, 0) AS withdrawal_48h_after_deposit_cnt_7d,
    COALESCE(wad.withdrawal_72h_after_deposit_cnt_7d, 0) AS withdrawal_72h_after_deposit_cnt_7d,

    COALESCE(ts.min_txn_amount, 0) AS min_txn_amount,
    COALESCE(ts.max_txn_amount, 0) AS max_txn_amount,
    COALESCE(ts.txn_cnt_last_1d, 0) AS txn_cnt_last_1d,
    COALESCE(ts.txn_amt_last_1d, 0) AS txn_amt_last_1d,
    COALESCE(ts.txn_amt_usd_last_1d, 0) AS txn_amt_usd_last_1d,

    COALESCE(w.crypto_withdrawal_volume_usdt_last_1d, 0) - COALESCE(d.crypto_deposit_volume_usdt_last_1d, 0) 
        AS crypto_withdrawal_net_volume_usdt_last_1d,

    COALESCE(p2p_dep.p2p_deposit_txn_amt_usdt_last_1d, 0) AS p2p_deposit_txn_amt_usdt_last_1d,
    COALESCE(p2p_dep.p2p_deposit_txn_amt_token_last_1d, 0) AS p2p_deposit_txn_amt_token_last_1d,

    COALESCE(p2p_wd.p2p_withdraw_txn_amt_usdt_last_1d, 0) AS p2p_withdraw_txn_amt_usdt_last_1d,
    COALESCE(p2p_wd.p2p_withdraw_txn_amt_token_last_1d, 0) AS p2p_withdraw_txn_amt_token_last_1d,

    COALESCE(buy_crypto.buy_crypto_txn_amt_usdt_last_1d, 0) AS buy_crypto_txn_amt_usdt_last_1d,
    COALESCE(buy_crypto.buy_crypto_txn_amt_token_last_1d, 0) AS buy_crypto_txn_amt_token_last_1d
FROM all_users u
LEFT JOIN deposit_freq_stats d ON u.user_id = d.deposit_freq_stats_user_id
LEFT JOIN withdraw_freq_stats w ON u.user_id = w.withdraw_freq_stats_user_id
LEFT JOIN withdrawal_after_deposit_cnt wad ON u.user_id = wad.user_id
LEFT JOIN transaction_stats ts ON u.user_id = ts.user_id
LEFT JOIN p2p_deposit_txn p2p_dep ON u.user_id = p2p_dep.user_id
LEFT JOIN p2p_withdraw_txn p2p_wd ON u.user_id = p2p_wd.user_id
LEFT JOIN buy_crypto_txn buy_crypto ON u.user_id = buy_crypto.user_id;
```


## Refactor

The goal is to convert a batch SQL that runs daily into something near-realtime by pre-computing state into SCD Type 2 dimension tables. Each dim table needs to capture the evolving state of a user's metrics so that at any point in time, you can look up the current value.


---

### Design Principles

Before diving in, a few key design decisions:

- **`valid_from` and `valid_to`** are Unix timestamps (13 digits bigint, ms), matching your example. `valid_to = 9999999999999` means "current/active record."
- Each dim table is keyed so that a point-in-time query is simply `WHERE key = X AND valid_from <= ts AND ts < valid_to`.
- The grain of each table is chosen to match how the source data changes — some are per-user, some are per-user-per-currency, etc.
- I'm separating tables by **source domain** rather than by downstream feature, because a single source change should update exactly one dim table, and features can be derived at query time.

---

### 1. `dim_market_rates`

**Source:** `ko_ods_market_rate`
**Grain:** one row per currency per valid period
**Changes when:** an exchange rate changes

| Column      | Type          | Description                                               |
|-------------|---------------|-----------------------------------------------------------|
| currency    | STRING        | Currency symbol (e.g., JPY, EUR)                          |
| rate_to_usd | DECIMAL(18,8) | 1 unit of currency = X USD                                |
| valid_from  | BIGINT        | Unix timestamp, start of validity                         |
| valid_to    | BIGINT        | Unix timestamp, end of validity (9999999999999 = current) |

This directly replaces the rate subquery in `transaction_base`.

---

### 2. `dim_user_master_mapping`

**Source:** `dim_okx_user_master_id_relation_df`
**Grain:** one row per user_id per valid period
**Changes when:** a user's master_user_id mapping changes

| Column         | Type   | Description    |
|----------------|--------|----------------|
| user_id        | STRING | User ID        |
| master_user_id | STRING | Master user ID |
| valid_from     | BIGINT | Unix timestamp |
| valid_to       | BIGINT | Unix timestamp |

Replaces the dimension join in `all_users`.

---

### 3. `dim_user_crypto_withdraw_stats`

**Source:** `dwd_okx_depositwithdraw_onchain_order_core_df` (business_type = 2)
**Grain:** one row per user_id per valid period
**Changes when:** any new withdrawal order is created or a withdrawal status changes for that user

This captures the **running aggregate state** that `withdraw_freq_stats` computes in batch.

| Column                           | Type          | Description                                          |
|----------------------------------|---------------|------------------------------------------------------|
| user_id                          | STRING        |                                                      |
| first_successful_withdrawal_time | TIMESTAMP     | MIN(create_time) where status=2                      |
| last_successful_withdrawal_time  | TIMESTAMP     | MAX(create_time) where status=2                      |
| first_withdrawal_volume_usdt     | DECIMAL(18,2) | USDT amount of the first successful withdrawal       |
| last_withdrawal_volume_usdt      | DECIMAL(18,2) | USDT amount of the most recent successful withdrawal |
| total_order_cnt                  | BIGINT        | COUNT(DISTINCT id), all statuses                     |
| total_usdt_amount                | DECIMAL(18,2) | SUM(volume_usdt), all statuses                       |
| total_distinct_addresses         | BIGINT        | COUNT(DISTINCT address)                              |
| successful_order_cnt             | BIGINT        | COUNT where status=2                                 |
| successful_usdt_amount           | DECIMAL(18,2) | SUM(volume_usdt) where status=2                      |
| distinct_currencies_all          | BIGINT        | COUNT(DISTINCT currency_id)                          |
| valid_from                       | BIGINT        | Unix timestamp                                       |
| valid_to                         | BIGINT        | Unix timestamp                                       |

**Why this design:** The batch SQL computes "last_1d" metrics by filtering on `days_window`. In a realtime model, you don't pre-filter by window — instead you maintain the running totals and derive windowed metrics either via a separate windowed table (see table 5 below) or at query time using the event stream. The lifetime/cumulative metrics (first/last timestamps, totals) fit naturally into SCD2.

---

### 4. `dim_user_crypto_deposit_stats`

**Source:** `dwd_okx_depositwithdraw_onchain_order_core_df` (business_type = 1)
**Grain:** one row per user_id per valid period
**Changes when:** any deposit order is created or status changes

Mirrors the withdrawal table structurally, with one extra field for deposit-specific logic.

| Column                        | Type          | Description                                    |
|-------------------------------|---------------|------------------------------------------------|
| user_id                       | STRING        |                                                |
| first_successful_deposit_time | TIMESTAMP     |                                                |
| last_successful_deposit_time  | TIMESTAMP     |                                                |
| first_deposit_volume_usdt     | DECIMAL(18,2) |                                                |
| last_deposit_volume_usdt      | DECIMAL(18,2) |                                                |
| total_order_cnt               | BIGINT        |                                                |
| total_usdt_amount             | DECIMAL(18,2) |                                                |
| total_distinct_addresses      | BIGINT        | COUNT(DISTINCT address)                        |
| total_distinct_from_addresses | BIGINT        | COUNT(DISTINCT from_address), deposit-specific |
| successful_order_cnt          | BIGINT        |                                                |
| successful_usdt_amount        | DECIMAL(18,2) |                                                |
| distinct_currencies_all       | BIGINT        |                                                |
| valid_from                    | BIGINT        |                                                |
| valid_to                      | BIGINT        |                                                |

---

### 5. `dim_user_crypto_withdraw_window_stats`

**Source:** same withdrawal stream
**Grain:** one row per user_id per valid period
**Changes when:** a withdrawal enters or exits a rolling window (event-driven or periodic micro-batch)

This handles the **rolling 1-day windowed metrics** that the batch SQL computes via `days_window`. In a near-realtime system, these are maintained by an event-driven process that increments on new events and decrements on window expiry.

| Column              | Type          | Description                                    |
|---------------------|---------------|------------------------------------------------|
| user_id             | STRING        |                                                |
| window_size         | STRING        | '1d' (extensible to '7d', '30d' if needed)     |
| order_cnt           | BIGINT        | total orders in window                         |
| volume_token        | DECIMAL(18,8) | SUM(total_volume_token) in window              |
| volume_usdt         | DECIMAL(18,2) | SUM(volume_usdt) in window                     |
| success_cnt         | BIGINT        | successful orders in window                    |
| success_usdt        | DECIMAL(18,2) | successful USDT in window                      |
| distinct_currencies | BIGINT        | distinct currency_id in window                 |
| distinct_tx_hashes  | BIGINT        | distinct transaction_hash, status=2, in window |
| valid_from          | BIGINT        |                                                |
| valid_to            | BIGINT        |                                                |

---

### 6. `dim_user_crypto_deposit_window_stats`

Mirror of table 5 for deposits, with one extra field.

| Column                  | Type          | Description      |
|-------------------------|---------------|------------------|
| user_id                 | STRING        |                  |
| window_size             | STRING        | '1d'             |
| order_cnt               | BIGINT        |                  |
| volume_token            | DECIMAL(18,8) |                  |
| volume_usdt             | DECIMAL(18,2) |                  |
| success_cnt             | BIGINT        |                  |
| success_usdt            | DECIMAL(18,2) |                  |
| distinct_currencies     | BIGINT        |                  |
| distinct_tx_hashes      | BIGINT        |                  |
| distinct_from_addresses | BIGINT        | deposit-specific |
| valid_from              | BIGINT        |                  |
| valid_to                | BIGINT        |                  |

---

### 7. `dim_user_withdrawal_after_deposit_stats`

**Source:** derived from the withdrawal + deposit event streams
**Grain:** one row per user_id per valid period
**Changes when:** a new withdrawal or deposit event causes any of the 9 counters to change

This replaces the expensive `joined_events` cross-join. Instead of recomputing all pairs, the realtime process maintains running counters.

| Column           | Type   | Description                                                 |
|------------------|--------|-------------------------------------------------------------|
| user_id          | STRING |                                                             |
| w24h_dep_cnt_7d  | BIGINT | withdrawals within 24h after deposit, withdrawal in last 7d |
| w48h_dep_cnt_7d  | BIGINT |                                                             |
| w72h_dep_cnt_7d  | BIGINT |                                                             |
| w24h_dep_cnt_30d | BIGINT |                                                             |
| w48h_dep_cnt_30d | BIGINT |                                                             |
| w72h_dep_cnt_30d | BIGINT |                                                             |
| w24h_dep_cnt_90d | BIGINT |                                                             |
| w48h_dep_cnt_90d | BIGINT |                                                             |
| w72h_dep_cnt_90d | BIGINT |                                                             |
| valid_from       | BIGINT |                                                             |
| valid_to         | BIGINT |                                                             |

**Implementation note:** This is the most complex to maintain in realtime. On each new withdrawal event, the process checks recent deposits (within 72h prior) for that user and increments counters. On window expiry (7d/30d/90d rolloff), counters decrement. This completely eliminates the M×N cross-join.

---

### 8. `dim_user_fiat_txn_stats`

**Source:** `ods_okx_payment_cash_deposit_order_core_df` + `ko_ods_market_rate`
**Grain:** one row per user_id per valid period
**Changes when:** a new fiat transaction is risk-approved or an existing one changes status

| Column         | Type          | Description                     |
|----------------|---------------|---------------------------------|
| user_id        | STRING        |                                 |
| min_txn_amount | DECIMAL(18,2) | lifetime min                    |
| max_txn_amount | DECIMAL(18,2) | lifetime max                    |
| txn_cnt_1d     | BIGINT        | risk-approved orders in last 1d |
| txn_amt_1d     | DECIMAL(18,2) | native amount sum, last 1d      |
| txn_amt_usd_1d | DECIMAL(18,2) | USD amount sum, last 1d         |
| valid_from     | BIGINT        |                                 |
| valid_to       | BIGINT        |                                 |

---

### 9. `dim_user_p2p_txn_stats`

**Source:** `dwd_okx_depositwithdraw_p2p_order_core_df`
**Grain:** one row per user_id per valid period
**Changes when:** a P2P order completes (order_status = 4) for the user as buyer or seller

| Column            | Type          | Description                |
|-------------------|---------------|----------------------------|
| user_id           | STRING        |                            |
| deposit_usdt_1d   | DECIMAL(18,2) | buy-side USDT in last 1d   |
| deposit_token_1d  | DECIMAL(18,8) | buy-side token in last 1d  |
| withdraw_usdt_1d  | DECIMAL(18,2) | sell-side USDT in last 1d  |
| withdraw_token_1d | DECIMAL(18,8) | sell-side token in last 1d |
| valid_from        | BIGINT        |                            |
| valid_to          | BIGINT        |                            |

**Note:** Buy and sell are combined into one table keyed by `user_id` because a user can be both buyer and seller, and the final query joins both to the same user row anyway. This avoids two separate lookups.

---

### 10. `dim_user_buy_crypto_stats`

**Source:** `dwd_okx_depositwithdraw_fiatgateway_order_core_df`
**Grain:** one row per user_id per valid period
**Changes when:** a fiat gateway order succeeds (status = 3)

| Column              | Type          | Description             |
|---------------------|---------------|-------------------------|
| user_id             | STRING        |                         |
| buy_crypto_usdt_1d  | DECIMAL(18,2) | USDT volume in last 1d  |
| buy_crypto_token_1d | DECIMAL(18,8) | token volume in last 1d |
| valid_from          | BIGINT        |                         |
| valid_to            | BIGINT        |                         |

---

### Summary: Source → Dim Table Mapping

| Original Source                      | Dim Table                                 | Update Trigger                              |
|--------------------------------------|-------------------------------------------|---------------------------------------------|
| `ko_ods_market_rate`                 | `dim_market_rates`                        | Rate change                                 |
| `dim_okx_user_master_id_relation_df` | `dim_user_master_mapping`                 | Mapping change                              |
| Onchain orders (withdraw)            | `dim_user_crypto_withdraw_stats`          | New/updated withdrawal                      |
| Onchain orders (withdraw)            | `dim_user_crypto_withdraw_window_stats`   | New withdrawal or window expiry             |
| Onchain orders (deposit)             | `dim_user_crypto_deposit_stats`           | New/updated deposit                         |
| Onchain orders (deposit)             | `dim_user_crypto_deposit_window_stats`    | New deposit or window expiry                |
| Onchain orders (both)                | `dim_user_withdrawal_after_deposit_stats` | New withdrawal or deposit, or window expiry |
| Cash deposit orders + rates          | `dim_user_fiat_txn_stats`                 | New fiat txn or rate change                 |
| P2P orders                           | `dim_user_p2p_txn_stats`                  | P2P order completion                        |
| Fiat gateway orders                  | `dim_user_buy_crypto_stats`               | Fiat gateway success                        |

The final realtime query becomes a simple point-in-time lookup across these 10 tables, all joined on `user_id` with `WHERE valid_from <= :query_ts AND :query_ts < valid_to`. No cross-joins, no full-table scans, no recomputation.