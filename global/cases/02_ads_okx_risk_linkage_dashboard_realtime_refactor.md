# Converting ads_okx_risk_linkage_dashboard_df to Realtime

## Original SQL Summary

**Purpose**: Risk linkage model dashboard metrics tracking the performance of a crypto withdrawal fraud detection system (linkage model) across different user entities and model types (supervised vs unsupervised).

**Current runtime**: Daily batch job processing 180 days of historical data

**Grain**: Per user_entity (4, 5, 7, 8) with aggregated metrics

**Key metrics computed**:
- Hit volume and rates (how many withdrawals were blocked)
- Coverage rates (how well the model covers known fraud cases)
- Precision rates (accuracy of blocks, 1 - false positive rate)
- Reviewed volumes (manual review statistics)
- Separate breakdowns for supervised (S) and unsupervised (Un-S) models
- Overall vs TRON-specific coverage rates

**Key challenges**:
1. **Expensive cross-joins**: Multiple LEFT JOINs across 6+ tables with complex date filtering
2. **Full table scans**: Processing 180 days of detection events, withdrawal orders, and case reviews
3. **Repeated MAX_PT lookups**: Multiple subqueries fetching latest partitions
4. **Complex multi-level aggregations**: Computing metrics that depend on overlapping populations (hit_online, reviewed, false positives)
5. **String ID operations**: JSON parsing and string concatenation for transaction pair matching

## Refactoring Strategy

### Design Principles

1. **ID format**: Convert all user_id and transaction identifiers to `uint64` format. Use an ID service for JSON-extracted string IDs.

2. **Time format**: All timestamps stored as 13-digit Unix epoch milliseconds (BIGINT). Add human-readable DateTime columns for debugging.

3. **Dimension tables**: Pre-compute incremental metrics using SCD Type 2 tables for evolving states, avoiding full historical scans.

4. **Event-driven updates**: Each source event (withdrawal detection, case review, recall report, address blacklist) triggers immediate dimension table updates.

5. **Separation by domain**:
   - Detection events → `dim_user_withdrawal_detection_stats`
   - Case reviews → `dim_user_case_review_stats`
   - Recall users → `dim_user_recall_flags`
   - Scam addresses → `dim_address_blacklist_flags`
   - Transaction pairs → `dim_user_withdrawal_txn_coverage`

6. **Eliminate expensive operations**:
   - Replace cross-table joins with pre-computed flags (if_recall_user, if_scam_address)
   - Maintain running counters instead of COUNT/SUM over 180-day windows
   - Use 180-day rolling window tables with increment/decrement logic

### Dimension Tables

#### 1. dim_user_withdrawal_detection_stats_180d

**Source**: `ods_okx_risk_rule_engine_detection_info_hi` (event_type = 'crypto_withdraw_pre_2fa_linkage_model')

**Grain**: One row per (user_entity, user_id) per valid period, tracking 180-day rolling window metrics

**Changes when**: New withdrawal detection event arrives or event exits 180-day window

**Table type**: SCD Type 2

| Column                        | Type          | Description                                                  |
|-------------------------------|---------------|--------------------------------------------------------------|
| user_entity                   | BIGINT        | User entity type (4, 5, 7, 8)                                |
| user_id                       | BIGINT(u64)   | Decrypted user ID                                            |
| total_txn_pairs               | BIGINT        | Distinct (user_id, wallet_address) pairs in 180d            |
| hit_vol_online                | BIGINT        | Count of detection_result = 'BLOCK' in 180d                 |
| hit_vol_s_offline             | BIGINT        | BLOCK with skynet_model_type = 'ONLINE_SUPERVISED'          |
| hit_vol_un_s_offline          | BIGINT        | BLOCK with skynet_model_type = 'ONLINE_UNSUPERVISED'        |
| hit_vol_tron_chain            | BIGINT        | BLOCK with chainId = 86 (TRON)                               |
| first_detection_time          | BIGINT        | Epoch ms of first detection in current window                |
| last_detection_time           | BIGINT        | Epoch ms of most recent detection                            |
| valid_from                    | BIGINT        | SCD2 validity start (epoch ms)                               |
| valid_to                      | BIGINT        | SCD2 validity end (9999999999999 = current)                  |
| updated_at                    | BIGINT        | Last update timestamp                                        |

**Note**: This table uses a 180-day sliding window. A background job decrements counters as events age out.

#### 2. dim_user_case_review_stats_180d

**Source**: `dwd_okx_risk_case_creation_info_hf` + `dwd_okx_risk_case_decision_postback_info_hf`

**Grain**: One row per (user_entity, user_id) per valid period, tracking case review outcomes in 180-day window

**Changes when**: New case review decision is posted or review exits 180-day window

**Table type**: SCD Type 2

| Column                        | Type          | Description                                                  |
|-------------------------------|---------------|--------------------------------------------------------------|
| user_entity                   | BIGINT        | User entity type                                             |
| user_id                       | BIGINT(u64)   | User ID                                                      |
| reviewed_vol                  | BIGINT        | Cases with any review decision (low/medium/high) in 180d     |
| fp_vol_low_risk               | BIGINT        | Cases marked as 'low' risk (false positives) in 180d        |
| reviewed_vol_s                | BIGINT        | Reviews for supervised model hits                            |
| fp_vol_s                      | BIGINT        | False positives for supervised model                         |
| reviewed_vol_un_s             | BIGINT        | Reviews for unsupervised model hits                          |
| fp_vol_un_s                   | BIGINT        | False positives for unsupervised model                       |
| hit_reviewed_vol              | BIGINT        | Cases that were both hit and reviewed                        |
| valid_from                    | BIGINT        | SCD2 validity start (epoch ms)                               |
| valid_to                      | BIGINT        | SCD2 validity end (9999999999999 = current)                  |
| updated_at                    | BIGINT        | Last update timestamp                                        |

#### 3. dim_user_recall_flags

**Source**: `ods_okx_risk_t_datavisor_detection_info_di` (event_type = 'push_payment_recall')

**Grain**: One row per user_id (users who have ever been recalled)

**Changes when**: User appears in a recall event (since 2024-04-19)

**Table type**: SCD Type 1 (once a user is recalled, flag persists)

| Column                        | Type          | Description                                                  |
|-------------------------------|---------------|--------------------------------------------------------------|
| user_id                       | BIGINT(u64)   | Primary key - decrypted user ID                              |
| is_recall_user                | TINYINT       | Always 1 (presence in table means recalled)                  |
| first_recall_date             | BIGINT        | Epoch ms of first recall event                               |
| last_recall_date              | BIGINT        | Epoch ms of most recent recall event                         |
| updated_at                    | BIGINT        | Last update timestamp                                        |

**Query optimization**: Realtime lookups join to this table; NULL match means not a recall user.

#### 4. dim_address_blacklist_flags

**Source**:
- `ods_okx_risk_chain_address_black_hf`
- `ads_okx_risk_arc_history_cases_df` (type = 2, withdraw_address)

**Grain**: One row per blockchain address

**Changes when**: Address is added to blacklist or identified as scam in ARC cases

**Table type**: SCD Type 2 (addresses can be blacklisted/delisted)

| Column                        | Type          | Description                                                  |
|-------------------------------|---------------|--------------------------------------------------------------|
| address                       | STRING        | Blockchain address (primary key)                             |
| is_blacklisted                | TINYINT       | 1 = currently blacklisted, 0 = delisted                      |
| blacklist_source              | STRING        | 'chain_address_black' or 'arc_history_cases'                 |
| first_blacklist_time          | BIGINT        | Epoch ms when first blacklisted                              |
| valid_from                    | BIGINT        | SCD2 validity start (epoch ms)                               |
| valid_to                      | BIGINT        | SCD2 validity end (9999999999999 = current)                  |
| updated_at                    | BIGINT        | Last update timestamp                                        |

#### 5. dim_user_withdrawal_txn_coverage_180d

**Source**: `dwd_okx_asset_deposit_withdraw_order_df` + enrichment from recall/blacklist flags

**Grain**: One row per (user_id, address, req_date) tracking whether transaction was covered by detection

**Changes when**: New withdrawal order created (since 2024-08-22) or order exits 180-day window

**Table type**: SCD Type 2

| Column                        | Type          | Description                                                  |
|-------------------------------|---------------|--------------------------------------------------------------|
| user_id                       | BIGINT(u64)   | Master user ID                                               |
| address                       | STRING        | Withdrawal address                                           |
| txn_pair_id                   | STRING        | '{user_id},{address}' for matching with detection events     |
| req_date                      | DATE          | Date of withdrawal request                                   |
| req_timestamp                 | BIGINT        | Epoch ms of withdrawal request                               |
| is_recall_user                | TINYINT       | 1 if user is in recall list at req_time                      |
| is_scam_address               | TINYINT       | 1 if address is blacklisted at req_time                      |
| chain_id                      | BIGINT        | Blockchain chain ID (86 = TRON)                              |
| was_detected                  | TINYINT       | 1 if this txn_pair appeared in detection events              |
| valid_from                    | BIGINT        | SCD2 validity start (epoch ms)                               |
| valid_to                      | BIGINT        | SCD2 validity end (9999999999999 = current)                  |
| updated_at                    | BIGINT        | Last update timestamp                                        |

**Purpose**: This table replaces the expensive t4+t5 join logic. It pre-computes recall/scam flags for all withdrawals, enabling fast coverage rate calculations.

#### 6. dim_user_entity_dashboard_metrics_180d

**Source**: Materialized aggregation from the above dimension tables

**Grain**: One row per user_entity, pre-aggregated dashboard metrics (the final output table)

**Changes when**: Any upstream dimension table updates

**Table type**: SCD Type 2

| Column                        | Type          | Description                                                  |
|-------------------------------|---------------|--------------------------------------------------------------|
| user_entity                   | BIGINT        | User entity type (4, 5, 7, 8)                                |
| total_vol                     | BIGINT        | Total withdrawal transaction pairs in 180d                   |
| hit_vol                       | BIGINT        | Total blocked transactions                                   |
| reviewed_vol                  | BIGINT        | Total reviewed cases                                         |
| hit_reviewed_vol              | BIGINT        | Transactions both hit and reviewed                           |
| hit_vol_daily_avg             | DOUBLE        | hit_vol / distinct_days                                      |
| hit_rate                      | DOUBLE        | hit_vol / total_vol                                          |
| coverage_rate_overall         | DOUBLE        | hit_vol / (hit_vol + missed_fraud_overall)                   |
| coverage_rate_tron            | DOUBLE        | hit_vol / (hit_vol + missed_fraud_tron_only)                 |
| precision_rate                | DOUBLE        | 1 - (fp_vol / hit_vol)                                       |
| s_hit_vol                     | BIGINT        | Supervised model hits                                        |
| s_reviewed_vol                | BIGINT        | Supervised model reviews                                     |
| s_hit_reviewed_vol            | BIGINT        | Supervised hits that were reviewed                           |
| s_hit_vol_daily_avg           | DOUBLE        | s_hit_vol / distinct_days                                    |
| s_hit_rate                    | DOUBLE        | s_hit_vol / total_vol                                        |
| s_coverage_rate               | DOUBLE        | s_hit_vol / (s_hit_vol + s_missed_fraud)                     |
| s_precision_rate              | DOUBLE        | 1 - (s_fp_vol / s_hit_vol)                                   |
| un_s_hit_vol                  | BIGINT        | Unsupervised model hits                                      |
| un_s_reviewed_vol             | BIGINT        | Unsupervised model reviews                                   |
| un_s_hit_reviewed_vol         | BIGINT        | Unsupervised hits that were reviewed                         |
| un_s_hit_vol_daily_avg        | DOUBLE        | un_s_hit_vol / distinct_days                                 |
| un_s_hit_rate                 | DOUBLE        | un_s_hit_vol / total_vol                                     |
| un_s_coverage_rate            | DOUBLE        | un_s_hit_vol / (un_s_hit_vol + un_s_missed_fraud)           |
| un_s_precision_rate           | DOUBLE        | 1 - (un_s_fp_vol / un_s_hit_vol)                            |
| distinct_days_with_activity   | BIGINT        | Count of distinct dates with events in window                |
| valid_from                    | BIGINT        | SCD2 validity start (epoch ms)                               |
| valid_to                      | BIGINT        | SCD2 validity end (9999999999999 = current)                  |
| updated_at                    | BIGINT        | Last update timestamp                                        |

### Source to Dimension Mapping

| Source Table/Stream                                      | Dimension Table(s)                              | Update Trigger                                    |
|----------------------------------------------------------|-------------------------------------------------|---------------------------------------------------|
| ods_okx_risk_rule_engine_detection_info_hi               | dim_user_withdrawal_detection_stats_180d        | New detection event or 180d expiry                |
| dwd_okx_risk_case_creation_info_hf                       | dim_user_case_review_stats_180d                 | New case created or review posted                 |
| dwd_okx_risk_case_decision_postback_info_hf              | dim_user_case_review_stats_180d                 | Case review decision posted or 180d expiry        |
| ods_okx_risk_t_datavisor_detection_info_di               | dim_user_recall_flags                           | New recall event (event_type = 'push_payment_recall') |
| ods_okx_risk_chain_address_black_hf                      | dim_address_blacklist_flags                     | Address added/removed from blacklist              |
| ads_okx_risk_arc_history_cases_df                        | dim_address_blacklist_flags                     | New ARC case with type=2 (withdraw_address)       |
| dwd_okx_asset_deposit_withdraw_order_df                  | dim_user_withdrawal_txn_coverage_180d           | New withdrawal order created or 180d expiry       |
| (All above dimension tables)                             | dim_user_entity_dashboard_metrics_180d          | Any dimension table update                        |

### Stream Processor Specifications

#### Processor: `update_dim_user_withdrawal_detection_stats_180d`

**Subscribes to**:
- `ods_okx_risk_rule_engine_detection_info_hi` (stream)
- `detection_event_expiry_scheduler` (180d window expiry)

**Trigger**: When a new withdrawal detection event arrives or event exits 180-day window

**Enrichment data** (KV lookups):
- None required (all data in detection event)

**Processing logic (increment)**:

1. Parse detection event:
   - Extract: `user_id`, `user_entity`, `skynet_model_type`, `chainId`, `decision_result`, `withdrawal_wallet_address`, `event_timestamp`
   - Validate: `event_type = 'crypto_withdraw_pre_2fa_linkage_model'`
   - Validate: `user_entity IN (4, 5, 7, 8)`
   - Validate: `wallet_address NOT REGEXP '\d{10,}' AND LENGTH >= 34`
   - Convert user_id to uint64 via ID service
   - Create `txn_pair_id = '{user_id},{wallet_address}'`

2. Look up current row for (user_entity, user_id) from `dim_user_withdrawal_detection_stats_180d` WHERE valid_to = 9999999999999

3. If no current row exists, initialize with zeros

4. Update counters:
   - Add txn_pair_id to distinct set, increment total_txn_pairs count
   - IF decision_result = 'BLOCK': increment hit_vol_online
   - IF decision_result = 'BLOCK' AND skynet_model_type = 'ONLINE_SUPERVISED': increment hit_vol_s_offline
   - IF decision_result = 'BLOCK' AND skynet_model_type = 'ONLINE_UNSUPERVISED': increment hit_vol_un_s_offline
   - IF decision_result = 'BLOCK' AND chainId = 86: increment hit_vol_tron_chain
   - Update first_detection_time (if NULL) and last_detection_time

5. Close current row: SET valid_to = event_timestamp

6. Insert new row with updated values, valid_from = event_timestamp, valid_to = 9999999999999

7. Schedule expiry task: "Decrement counters at event_timestamp + 180 days"

**Processing logic (decrement)**:

1. On expiry event (detection exits 180d window):
2. Look up current row for (user_entity, user_id)
3. Decrement corresponding counters (reverse the increment logic)
4. Remove txn_pair_id from distinct set, decrement total_txn_pairs
5. Close current row, insert new row with decremented counts

**Emits**: `dim_user_withdrawal_detection_stats_updated` event
- Payload: { user_entity, user_id, event_timestamp, counters_changed }

**Idempotency**: Use (event_id, user_id, event_timestamp) as dedup key. Store in idempotency log for 24h.

**Dependencies**: None

---

#### Processor: `update_dim_user_case_review_stats_180d`

**Subscribes to**:
- `dwd_okx_risk_case_creation_info_hf` (stream)
- `dwd_okx_risk_case_decision_postback_info_hf` (stream)
- `case_review_expiry_scheduler` (180d window expiry)

**Trigger**: When a case is created, reviewed, or exits 180-day window

**Enrichment data** (KV lookups):
- `dim_user_withdrawal_detection_stats_180d` (to determine if case was from S or Un-S model)

**Processing logic (increment)**:

1. Parse case review event:
   - From creation: extract user_id, event_id, create_time, event_type
   - From decision postback: extract event_id, case_review_decision, postback_time
   - Validate: event_type = 'crypto_withdraw_pre_2fa_linkage_model'
   - For duplicate decisions on same event_id, use ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY postback_time DESC) = 1

2. Join creation + decision by event_id to get (user_id, case_review_decision, create_time)

3. Look up user_entity from original detection event (or store it in case_creation_info)

4. Look up current row for (user_entity, user_id) from dim_user_case_review_stats_180d WHERE valid_to = 9999999999999

5. If no current row exists, initialize with zeros

6. Update counters based on case_review_decision:
   - IF case_review_decision IN ('low', 'medium', 'high'): increment reviewed_vol
   - IF case_review_decision = 'low': increment fp_vol_low_risk
   - Look up skynet_model_type from detection event (via event_id linkage)
   - IF model = 'ONLINE_SUPERVISED':
     - IF reviewed: increment reviewed_vol_s
     - IF low: increment fp_vol_s
   - IF model = 'ONLINE_UNSUPERVISED':
     - IF reviewed: increment reviewed_vol_un_s
     - IF low: increment fp_vol_un_s
   - IF reviewed: increment hit_reviewed_vol

7. Close current row: SET valid_to = create_time

8. Insert new row with updated values, valid_from = create_time, valid_to = 9999999999999

9. Schedule expiry task: "Decrement counters at create_time + 180 days"

**Processing logic (decrement)**:

1. On expiry event (review exits 180d window):
2. Look up current row for (user_entity, user_id)
3. Decrement corresponding counters (reverse the increment logic)
4. Close current row, insert new row with decremented counts

**Emits**: `dim_user_case_review_stats_updated` event
- Payload: { user_entity, user_id, event_timestamp, counters_changed }

**Idempotency**: Use (event_id, user_id, postback_time) as dedup key.

**Dependencies**: Requires detection event data to determine model type and user_entity.

---

#### Processor: `update_dim_user_recall_flags`

**Subscribes to**: `ods_okx_risk_t_datavisor_detection_info_di` (stream)

**Trigger**: When a recall event is detected (event_type = 'push_payment_recall', entity_type = 'public_order_id')

**Enrichment data** (KV lookups):
- Decryption service (convert user_id to uint64)

**Processing logic**:

1. Parse recall event:
   - Extract: user_id from input_param JSON
   - Validate: event_type = 'push_payment_recall'
   - Validate: entity_type = 'public_order_id'
   - Validate: event_time >= '2024-04-19'
   - Decrypt user_id via `DEC_USER_ID_2_RISK_DECRYPTION_ID` function

2. Look up existing row for user_id from dim_user_recall_flags

3. If row exists:
   - Update last_recall_date = event_timestamp
   - Update updated_at = event_timestamp

4. If row does not exist:
   - Insert new row:
     - user_id = decrypted_user_id
     - is_recall_user = 1
     - first_recall_date = event_timestamp
     - last_recall_date = event_timestamp
     - updated_at = event_timestamp

**Emits**: `dim_user_recall_flags_updated` event
- Payload: { user_id, is_new_recall, event_timestamp }

**Idempotency**: Use (event_id, user_id) as dedup key.

**Dependencies**: Requires user ID decryption service.

**Note**: This is SCD Type 1 because once a user is recalled, the flag persists (no valid_to/valid_from).

---

#### Processor: `update_dim_address_blacklist_flags`

**Subscribes to**:
- `ods_okx_risk_chain_address_black_hf` (stream)
- `ads_okx_risk_arc_history_cases_df` (stream)

**Trigger**: When an address is added to blacklist or identified in ARC cases

**Enrichment data** (KV lookups):
- None required

**Processing logic (from chain_address_black)**:

1. Parse blacklist event:
   - Extract: address, note, event_timestamp
   - Previously had filter: `note REGEXP '^Fraud Risk Op'` (removed as of 09/26 update)

2. Look up current row for address from dim_address_blacklist_flags WHERE valid_to = 9999999999999

3. If no current row exists OR current row has is_blacklisted = 0:
   - Close existing row if present (set valid_to = event_timestamp)
   - Insert new row:
     - address = address
     - is_blacklisted = 1
     - blacklist_source = 'chain_address_black'
     - first_blacklist_time = event_timestamp (if new address)
     - valid_from = event_timestamp
     - valid_to = 9999999999999
     - updated_at = event_timestamp

4. If address is removed from blacklist (deletion event):
   - Close current row: SET valid_to = event_timestamp
   - Insert new row with is_blacklisted = 0

**Processing logic (from ARC cases)**:

1. Parse ARC case event:
   - Extract: withdraw_address, type, event_timestamp
   - Validate: type = 2

2. Look up current row for withdraw_address (as address) WHERE valid_to = 9999999999999

3. If no current row exists OR current row has is_blacklisted = 0:
   - Close existing row if present
   - Insert new row:
     - address = withdraw_address
     - is_blacklisted = 1
     - blacklist_source = 'arc_history_cases'
     - first_blacklist_time = event_timestamp (if new)
     - valid_from = event_timestamp
     - valid_to = 9999999999999

**Emits**: `dim_address_blacklist_flags_updated` event
- Payload: { address, is_blacklisted, blacklist_source, event_timestamp }

**Idempotency**: Use (address, event_timestamp, source) as dedup key.

**Dependencies**: None

---

#### Processor: `update_dim_user_withdrawal_txn_coverage_180d`

**Subscribes to**:
- `dwd_okx_asset_deposit_withdraw_order_df` (stream)
- `dim_user_recall_flags_updated` (to refresh recall flags)
- `dim_address_blacklist_flags_updated` (to refresh blacklist flags)
- `dim_user_withdrawal_detection_stats_updated` (to mark was_detected)
- `withdrawal_txn_expiry_scheduler` (180d window expiry)

**Trigger**: New withdrawal order created (since 2024-08-22) or order exits 180-day window

**Enrichment data** (KV lookups):
- `dim_user_recall_flags` (is user recalled at req_time)
- `dim_address_blacklist_flags` (is address blacklisted at req_time)
- `dim_user_withdrawal_detection_stats_180d` (was this txn_pair detected)

**Processing logic (increment)**:

1. Parse withdrawal order event:
   - Extract: master_user_id, address, create_time, chain_id
   - Validate: create_time >= '2024-08-22'
   - Create txn_pair_id = '{master_user_id},{address}'
   - req_date = TO_DATE(create_time)
   - req_timestamp = create_time (epoch ms)

2. Enrich with recall flag:
   - Look up dim_user_recall_flags for master_user_id
   - is_recall_user = 1 if found, else 0

3. Enrich with blacklist flag:
   - Look up dim_address_blacklist_flags for address WHERE valid_from <= req_timestamp AND req_timestamp < valid_to
   - is_scam_address = 1 if found AND is_blacklisted = 1, else 0

4. Check if detected:
   - Look up dim_user_withdrawal_detection_stats_180d for (master_user_id, address) txn_pair on req_date
   - was_detected = 1 if found in detection events, else 0

5. Insert row into dim_user_withdrawal_txn_coverage_180d:
   - user_id = master_user_id
   - address = address
   - txn_pair_id = txn_pair_id
   - req_date = req_date
   - req_timestamp = req_timestamp
   - is_recall_user = is_recall_user
   - is_scam_address = is_scam_address
   - chain_id = chain_id
   - was_detected = was_detected
   - valid_from = req_timestamp
   - valid_to = 9999999999999

6. Schedule expiry task: "Remove txn from coverage table at req_timestamp + 180 days"

**Processing logic (decrement)**:

1. On expiry event (withdrawal exits 180d window):
2. Look up row for (user_id, address, req_date)
3. Close row: SET valid_to = expiry_timestamp

**Processing logic (on recall/blacklist flag update)**:

1. When dim_user_recall_flags_updated event arrives:
   - Query dim_user_withdrawal_txn_coverage_180d for all rows with user_id = updated_user_id AND valid_to = 9999999999999
   - For each row: close it, insert new row with is_recall_user = 1

2. When dim_address_blacklist_flags_updated event arrives:
   - Query dim_user_withdrawal_txn_coverage_180d for all rows with address = updated_address AND valid_to = 9999999999999
   - For each row: close it, insert new row with updated is_scam_address value

**Emits**: `dim_user_withdrawal_txn_coverage_updated` event
- Payload: { user_id, address, req_date, is_recall_user, is_scam_address, was_detected }

**Idempotency**: Use (user_id, address, req_timestamp) as dedup key.

**Dependencies**:
- Requires dim_user_recall_flags and dim_address_blacklist_flags to be current
- Requires dim_user_withdrawal_detection_stats_180d for detection matching

**Note**: This table is the key to eliminating the expensive t4+t5 join. It pre-computes coverage flags for all withdrawals.

---

#### Processor: `update_dim_user_entity_dashboard_metrics_180d`

**Subscribes to**:
- `dim_user_withdrawal_detection_stats_updated`
- `dim_user_case_review_stats_updated`
- `dim_user_withdrawal_txn_coverage_updated`

**Trigger**: When any upstream dimension table updates

**Enrichment data** (KV lookups):
- `dim_user_withdrawal_detection_stats_180d` (all rows for user_entity)
- `dim_user_case_review_stats_180d` (all rows for user_entity)
- `dim_user_withdrawal_txn_coverage_180d` (all rows for user_entity)

**Processing logic**:

This processor materializes the final dashboard aggregations by joining the upstream dimension tables. It replicates the logic from t8 → final SELECT in the original SQL.

1. On trigger event (user_entity affected):
   - Extract user_entity from trigger event

2. Query dim_user_withdrawal_detection_stats_180d:
   - Aggregate by user_entity WHERE valid_to = 9999999999999:
     - SUM(total_txn_pairs) → total_vol
     - SUM(hit_vol_online) → hit_vol
     - SUM(hit_vol_s_offline) → s_hit_vol
     - SUM(hit_vol_un_s_offline) → un_s_hit_vol
   - COUNT(DISTINCT req_date) → distinct_days_with_activity

3. Query dim_user_case_review_stats_180d:
   - Aggregate by user_entity WHERE valid_to = 9999999999999:
     - SUM(reviewed_vol) → reviewed_vol
     - SUM(fp_vol_low_risk) → fp_vol
     - SUM(reviewed_vol_s) → s_reviewed_vol
     - SUM(fp_vol_s) → s_fp_vol
     - SUM(reviewed_vol_un_s) → un_s_reviewed_vol
     - SUM(fp_vol_un_s) → un_s_fp_vol
     - SUM(hit_reviewed_vol) → hit_reviewed_vol

4. Query dim_user_withdrawal_txn_coverage_180d:
   - Aggregate by user_entity WHERE valid_to = 9999999999999:
   - For coverage rate calculation (Overall):
     - IF user_entity IN (7, 8): missed_fraud_overall = COUNT rows WHERE was_detected = 0 AND is_recall_user = 1 AND is_scam_address = 1
     - IF user_entity IN (4, 5): missed_fraud_overall = COUNT rows WHERE was_detected = 0 AND is_scam_address = 1
   - For coverage rate calculation (TRON):
     - IF user_entity IN (7, 8): missed_fraud_tron = COUNT rows WHERE was_detected = 0 AND chain_id = 86 AND is_recall_user = 1 AND is_scam_address = 1
     - IF user_entity IN (4, 5): missed_fraud_tron = COUNT rows WHERE was_detected = 0 AND chain_id = 86 AND is_scam_address = 1

5. Compute derived metrics:
   - hit_vol_daily_avg = hit_vol / distinct_days_with_activity
   - hit_rate = hit_vol / total_vol
   - coverage_rate_overall = hit_vol / (hit_vol + missed_fraud_overall)
   - coverage_rate_tron = hit_vol / (hit_vol + missed_fraud_tron)
   - precision_rate = 1 - (fp_vol / hit_vol)
   - s_hit_vol_daily_avg = s_hit_vol / distinct_days_with_activity
   - s_hit_rate = s_hit_vol / total_vol
   - s_coverage_rate = s_hit_vol / (s_hit_vol + s_missed_fraud)
   - s_precision_rate = 1 - (s_fp_vol / s_hit_vol)
   - un_s_hit_vol_daily_avg = un_s_hit_vol / distinct_days_with_activity
   - un_s_hit_rate = un_s_hit_vol / total_vol
   - un_s_coverage_rate = un_s_hit_vol / (un_s_hit_vol + un_s_missed_fraud)
   - un_s_precision_rate = 1 - (un_s_fp_vol / un_s_hit_vol)

6. Look up current row for user_entity from dim_user_entity_dashboard_metrics_180d WHERE valid_to = 9999999999999

7. If values changed:
   - Close current row: SET valid_to = current_timestamp
   - Insert new row with updated metrics, valid_from = current_timestamp, valid_to = 9999999999999

**Emits**: `dim_user_entity_dashboard_metrics_updated` event
- Payload: { user_entity, metrics_snapshot, updated_at }

**Idempotency**: Use (user_entity, update_timestamp) as dedup key.

**Dependencies**: All upstream dimension tables must be current.

**Note**: This processor can be optimized by maintaining incremental counters rather than re-aggregating, but re-aggregation is simpler and sufficient if update frequency is low (e.g., <100 updates/sec per user_entity).

---

### Data Flow Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Source Event Streams                            │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
        ┌──────────────────────────┼──────────────────────────────────────┐
        │                          │                                      │
        ▼                          ▼                                      ▼
┌───────────────────┐   ┌───────────────────────┐        ┌───────────────────────┐
│ detection_info_hi │   │ case_creation_info_hf │        │  withdrawal_order_df  │
│                   │   │ case_decision_postback│        │                       │
└────────┬──────────┘   └─────────┬─────────────┘        └──────────┬────────────┘
         │                        │                                  │
         │                        │                                  │
         ▼                        ▼                                  ▼
    ┌─────────────────┐   ┌──────────────────┐         ┌──────────────────────────┐
    │  Processor:     │   │  Processor:      │         │  Processor:              │
    │  update_dim_    │   │  update_dim_     │         │  update_dim_             │
    │  withdrawal_    │   │  case_review_    │         │  withdrawal_txn_         │
    │  detection_     │   │  stats_180d      │         │  coverage_180d           │
    │  stats_180d     │   │                  │         │                          │
    └────────┬────────┘   └────────┬─────────┘         └────────┬─────────────────┘
             │                     │                             │
             │                     │                             │
             ▼                     ▼                             ▼
    ┌─────────────────────┐ ┌────────────────────┐  ┌──────────────────────────────┐
    │ dim_user_withdrawal │ │ dim_user_case_     │  │ dim_user_withdrawal_txn_     │
    │ _detection_stats_   │ │ review_stats_180d  │  │ coverage_180d (SCD2)         │
    │ 180d (SCD2)         │ │ (SCD2)             │  │                              │
    └─────────┬───────────┘ └──────────┬─────────┘  └────────┬─────────────────────┘
              │                        │                      │
              └────────────────────────┼──────────────────────┘
                                       │
                                       ▼
                          ┌─────────────────────────────┐
                          │  Processor:                 │
                          │  update_dim_user_entity_    │
                          │  dashboard_metrics_180d     │
                          └──────────────┬──────────────┘
                                         │
                                         ▼
                          ┌─────────────────────────────────┐
                          │ dim_user_entity_dashboard_      │
                          │ metrics_180d (SCD2)             │
                          │ [FINAL OUTPUT TABLE]            │
                          └─────────────────────────────────┘

┌──────────────────────┐         ┌────────────────────────┐
│ recall_events        │         │ blacklist_events       │
│ (datavisor)          │         │ (chain_addr_black +    │
└──────┬───────────────┘         │  arc_history_cases)    │
       │                         └────────┬───────────────┘
       │                                  │
       ▼                                  ▼
┌──────────────────┐           ┌──────────────────────────┐
│  Processor:      │           │  Processor:              │
│  update_dim_     │           │  update_dim_address_     │
│  user_recall_    │           │  blacklist_flags         │
│  flags           │           │                          │
└────────┬─────────┘           └────────┬─────────────────┘
         │                              │
         ▼                              ▼
┌──────────────────┐           ┌──────────────────────────┐
│ dim_user_recall_ │           │ dim_address_blacklist_   │
│ flags (SCD1)     │           │ flags (SCD2)             │
└────────┬─────────┘           └────────┬─────────────────┘
         │                              │
         └──────────────┬───────────────┘
                        │
           [Used for enrichment in withdrawal_txn_coverage processor]
```

**Key dependencies**:
1. `dim_user_recall_flags` and `dim_address_blacklist_flags` must be updated before `dim_user_withdrawal_txn_coverage_180d`
2. All upstream dimension tables must be current before `dim_user_entity_dashboard_metrics_180d` updates
3. Background expiry schedulers decrement counters for 180-day rolling windows

---

### Realtime Query Pattern

The final realtime query becomes a simple point-in-time lookup:

```sql
-- Get dashboard metrics for all user entities at query_time
SELECT
  user_entity,
  total_vol,
  hit_vol,
  reviewed_vol,
  hit_reviewed_vol,
  hit_vol_daily_avg,
  hit_rate,
  coverage_rate_overall,
  coverage_rate_tron,
  precision_rate,
  s_hit_vol,
  s_reviewed_vol,
  s_hit_reviewed_vol,
  s_hit_vol_daily_avg,
  s_hit_rate,
  s_coverage_rate,
  s_precision_rate,
  un_s_hit_vol,
  un_s_reviewed_vol,
  un_s_hit_reviewed_vol,
  un_s_hit_vol_daily_avg,
  un_s_hit_rate,
  un_s_coverage_rate,
  un_s_precision_rate
FROM dim_user_entity_dashboard_metrics_180d
WHERE valid_from <= :query_ts
  AND :query_ts < valid_to
ORDER BY user_entity;
```

**Query performance**:
- No joins required
- No aggregations required (pre-aggregated)
- Simple index scan on (user_entity, valid_from, valid_to)
- Expected latency: <10ms

**Comparison to batch SQL**:
- **Batch**: 1+ hour full table scan across 6 tables, 180 days of data, complex multi-level aggregations
- **Realtime**: <10ms index lookup on pre-computed dimension table

---

## Implementation Notes

### 1. Idempotency

All processors must implement event deduplication:
- Use composite dedup keys: (event_id, user_id, timestamp)
- Store dedup log in Redis with 24-hour TTL
- Before processing event, check: `IF EXISTS dedup_key THEN skip`
- After processing event, set: `SET dedup_key 1 EX 86400`

### 2. Ordering

**Critical constraint**: Events must be processed in timestamp order to maintain correct SCD Type 2 history.

**Implementation**:
- Use event_timestamp (from source data), NOT processing_timestamp
- Partition Kafka/Pulsar topics by (user_entity, user_id) to maintain order per user
- Use timestamp-based watermarks for late arrival handling (see below)

### 3. Late Arrivals

**Problem**: Events may arrive out-of-order (e.g., detection event arrives after case review event).

**Solution**:
- Implement watermark-based processing (e.g., "process events up to T-5min")
- Buffer events in 5-minute window, sort by event_timestamp before processing
- For very late events (>5min delay):
  - Query existing dimension table rows in affected time range
  - Retroactively close/update rows to insert late event
  - Recompute downstream metrics from that point forward
  - Emit correction event to downstream processors

**Trade-off**: Late events require more complex "backfill" logic. Consider setting a cutoff (e.g., reject events >1 hour late) based on business requirements.

### 4. Backfill

For historical data migration:

**Phase 1: Initial backfill (offline)**
1. Run batch jobs to populate dimension tables from 180 days of historical data
2. Process events in chronological order to build correct SCD Type 2 history
3. Validate: compare realtime dimension tables to batch SQL output

**Phase 2: Shadow mode (dual-write)**
1. Deploy stream processors in "shadow mode":
   - Processors write to dimension tables
   - Dashboard still reads from batch tables
2. Run for 7 days, compare realtime vs batch outputs daily
3. Investigate discrepancies, fix processor bugs

**Phase 3: Cutover**
1. Switch dashboard queries to read from realtime dimension tables
2. Monitor query latency and correctness
3. Gradually ramp up traffic (10% → 50% → 100% over 3 days)

**Phase 4: Decommission batch**
1. Once realtime is stable for 14 days, decommission batch SQL job
2. Archive batch tables for audit

### 5. Monitoring

**Key metrics to track**:

| Metric                                  | Alert Threshold               | Description                                      |
|-----------------------------------------|-------------------------------|--------------------------------------------------|
| Processor lag (event_time - process_time)| >5 minutes                    | Indicates backlog in stream processing           |
| Dedup cache hit rate                    | <95%                          | Low hit rate suggests duplicate events           |
| Dimension table growth rate             | >10% per day                  | Unexpected growth may indicate data quality issue|
| Late arrival rate                       | >1% of events                 | High late arrival rate may require tuning        |
| SCD2 row churn rate                     | -                             | Tracks frequency of dimension updates            |
| Query latency (p99)                     | >50ms                         | Indicates index or query optimization needed     |
| Processor error rate                    | >0.1%                         | Any errors should be investigated immediately    |
| Data freshness (current_time - max(valid_from))| >10 minutes              | Indicates stale data, processor may be down      |

**Logging**:
- Log all processor errors with full event payload for debugging
- Log dimension table updates with before/after values for audit trail
- Log late arrival events for analysis

**Alerting**:
- Page on-call if processor lag >15 minutes or error rate >1%
- Slack alert if data freshness >10 minutes
- Daily report of data quality metrics (late arrivals, dedup hit rate, etc.)

### 6. Data Quality

**Validation checks** (run hourly):

1. **Referential integrity**:
   - All user_ids in dim_user_withdrawal_detection_stats should exist in dim_user_withdrawal_txn_coverage
   - All addresses in dim_user_withdrawal_txn_coverage should exist in dim_address_blacklist_flags (if is_scam_address = 1)

2. **SCD2 validity**:
   - No gaps in valid_from/valid_to ranges (valid_to[i] = valid_from[i+1])
   - Exactly one row with valid_to = 9999999999999 per entity

3. **Counter consistency**:
   - total_vol >= hit_vol (can't have more hits than total transactions)
   - hit_vol >= hit_reviewed_vol (can't review more than hit)
   - hit_vol >= fp_vol (can't have more false positives than hits)

4. **Comparison to batch** (during shadow mode):
   - Run batch SQL job nightly
   - Compare batch output to realtime dimension table aggregations
   - Alert if any metric differs by >5%

---

## Migration Plan

### Week 1-2: Design & Schema Creation

1. **Day 1-3**: Design review
   - Review this architecture doc with stakeholders
   - Validate business logic mappings (coverage rate formulas, precision rate, etc.)
   - Confirm ID format and time format conventions

2. **Day 4-7**: Schema implementation
   - Create dimension tables in staging environment
   - Define indexes: (user_entity, user_id, valid_from, valid_to) for SCD2 tables
   - Set up partitioning for large tables (e.g., dim_user_withdrawal_txn_coverage_180d)

3. **Day 8-10**: Backfill scripts
   - Write batch jobs to populate dimension tables from 180 days of historical data
   - Process in chronological order to build correct SCD Type 2 history
   - Validate output against batch SQL

### Week 3-4: Processor Development

1. **Day 11-14**: Core processors
   - Implement processors for detection_stats, case_review_stats, recall_flags, blacklist_flags
   - Unit tests for each processor (mock events, verify dimension table state)
   - Integration tests with local Kafka/Pulsar

2. **Day 15-18**: Coverage & aggregation processors
   - Implement withdrawal_txn_coverage processor (most complex due to enrichment)
   - Implement dashboard_metrics aggregation processor
   - Test late arrival handling and out-of-order events

3. **Day 19-21**: Expiry logic
   - Implement 180-day window expiry schedulers
   - Test increment/decrement logic for rolling windows
   - Validate counter consistency (increment, wait 180d, decrement → should return to zero)

### Week 5-6: Shadow Mode Testing

1. **Day 22-28**: Deploy to staging
   - Deploy all processors to staging environment
   - Connect to staging Kafka/Pulsar topics (replayed from production)
   - Run backfill to populate historical data

2. **Day 29-35**: Shadow mode in production
   - Deploy processors to production (write-only mode)
   - Dashboard continues reading from batch tables
   - Daily comparison: realtime metrics vs batch metrics
   - Investigate and fix discrepancies

3. **Day 36-42**: Performance tuning
   - Optimize slow processors (add caching, batch writes, etc.)
   - Tune Kafka/Pulsar consumer configs (prefetch, batch size)
   - Load test: simulate peak traffic (10x normal volume)

### Week 7-8: Cutover & Stabilization

1. **Day 43-45**: Gradual cutover
   - Switch 10% of dashboard queries to read from realtime tables
   - Monitor query latency (p50, p99) and error rate
   - Validate correctness with spot checks

2. **Day 46-49**: Ramp up traffic
   - Increase to 50% of queries on realtime tables
   - Continue monitoring, validate no regressions
   - Increase to 100% of queries on realtime tables

3. **Day 50-56**: Stabilization
   - Monitor for 7 days at 100% traffic
   - Fine-tune alerts and dashboards
   - Document runbook for on-call (common issues, recovery procedures)

### Week 9: Decommission Batch

1. **Day 57-60**: Archive batch job
   - Disable batch SQL job (keep code in repo for emergency rollback)
   - Archive batch tables for audit (retain for 1 year)
   - Update documentation to reflect new architecture

2. **Day 61-63**: Cleanup
   - Remove unused code and scripts
   - Archive design docs and migration logs
   - Post-mortem: lessons learned, improvements for next migration

---

## Expected Improvements

### Performance Gains

| Metric                          | Before (Batch)      | After (Realtime)     | Improvement       |
|---------------------------------|---------------------|----------------------|-------------------|
| Data freshness                  | 24 hours            | <1 minute            | 1440x faster      |
| Query latency                   | 1+ hour             | <10ms                | 360,000x faster   |
| Resource utilization            | Daily burst         | Constant low load    | Smoother load     |
| Scalability                     | Limited by batch    | Scales with events   | Near-linear scale |

### Cost Savings

**Batch job costs** (estimated):
- Compute: 1 hour × 1000 cores × $0.10/core-hour = $100/day = $36,500/year
- Storage: 180 days × 100GB/day × $0.05/GB = $900/month = $10,800/year
- **Total batch cost**: ~$47,300/year

**Realtime processing costs** (estimated):
- Stream processors: 10 processors × 24 hours × 4 cores × $0.05/core-hour = $48/day = $17,520/year
- Dimension table storage: 50GB × $0.10/GB = $5/month = $60/year
- Kafka/Pulsar: $500/month = $6,000/year
- **Total realtime cost**: ~$23,580/year

**Net savings**: $47,300 - $23,580 = **$23,720/year (50% cost reduction)**

### Operational Benefits

1. **Faster fraud detection**: Dashboard metrics updated in realtime, enabling faster model tuning and incident response

2. **Improved model accuracy**: Near-instant feedback loop between detection and review allows faster iteration on supervised/unsupervised models

3. **Reduced data staleness**: No more "waiting for daily batch" — metrics reflect current state within 1 minute

4. **Better auditability**: SCD Type 2 history provides full audit trail of metric changes over time

5. **Simplified architecture**: Eliminates complex batch job dependencies, MAX_PT logic, and daily scheduling

---

## Appendix: Complex Logic Details

### Coverage Rate Calculation

The original SQL computes coverage rate as:

```
Coverage Rate (Overall) = Hit Vol / (Hit Vol + Missed Fraud)
```

Where **Missed Fraud** = transactions that:
- Were NOT detected (if_hit_online = 0)
- AND involved a recall user AND a scam address (for user_entity 7, 8)
- OR involved a scam address (for user_entity 4, 5)

**Realtime implementation**:

From `dim_user_withdrawal_txn_coverage_180d`, compute:

```sql
missed_fraud_overall = SUM(
  CASE
    WHEN user_entity IN (7, 8) AND was_detected = 0
      THEN IF(is_recall_user = 1 AND is_scam_address = 1, 1, 0)
    WHEN user_entity IN (4, 5) AND was_detected = 0
      THEN IF(is_scam_address = 1, 1, 0)
    ELSE 0
  END
)

coverage_rate_overall = hit_vol / (hit_vol + missed_fraud_overall)
```

**TRON-specific coverage**:

Similar logic but only counts missed fraud on TRON chain (chain_id = 86).

### Precision Rate Calculation

The original SQL computes precision rate as:

```
Precision Rate = 1 - (FP Vol / Hit Vol)
```

Where **FP Vol** = cases that were hit (decision_result = 'BLOCK') AND reviewed as 'low' risk.

**Important note from 09/09 update**: Precision is calculated on the **reject base** (all hits), not the review base. If 100 cases were blocked and 10 were marked low risk (out of 30 reviewed), precision = 1 - 10/100 = 90%, NOT 1 - 10/30 = 66%.

**Realtime implementation**:

From `dim_user_case_review_stats_180d`:
- `fp_vol_low_risk` = count of cases reviewed as 'low'
- From `dim_user_withdrawal_detection_stats_180d`: `hit_vol_online` = count of blocks

```
precision_rate = 1 - (fp_vol_low_risk / hit_vol_online)
```

### Supervised vs Unsupervised Split

The original SQL uses `skynet_model_type`:
- `'ONLINE_SUPERVISED'` → S metrics
- `'ONLINE_UNSUPERVISED'` → Un-S metrics

**Realtime implementation**:

In `dim_user_withdrawal_detection_stats_180d`, maintain separate counters:
- `hit_vol_s_offline` for supervised hits
- `hit_vol_un_s_offline` for unsupervised hits

In `dim_user_case_review_stats_180d`, maintain separate counters:
- `reviewed_vol_s`, `fp_vol_s` for supervised reviews
- `reviewed_vol_un_s`, `fp_vol_un_s` for unsupervised reviews

### Transaction Pair Matching

The original SQL creates transaction pairs as:

```sql
txn_pair = CONCAT_WS(',', user_id, withdrawal_wallet_address)
```

And uses this to join detection events with withdrawal orders.

**Realtime implementation**:

In both `dim_user_withdrawal_detection_stats_180d` and `dim_user_withdrawal_txn_coverage_180d`, store:
- `txn_pair_id = '{user_id},{address}'` (string)

When a detection event arrives, check if corresponding withdrawal order exists in coverage table:
- Query `dim_user_withdrawal_txn_coverage_180d` WHERE `txn_pair_id = event.txn_pair_id` AND `req_date = event.req_date`
- If found, mark `was_detected = 1`

When a withdrawal order arrives, create coverage row with `was_detected = 0`, then check if detection event exists:
- Query `dim_user_withdrawal_detection_stats_180d` for matching `txn_pair_id` on `req_date`
- If found, update coverage row: `was_detected = 1`

This bidirectional update ensures coverage is accurate regardless of event arrival order.

---

## Summary

This refactoring converts a complex 180-day batch aggregation job into a realtime stream processing architecture by:

1. **Pre-computing state** in SCD Type 2 dimension tables (detection stats, case reviews, recall flags, blacklist flags, transaction coverage)

2. **Eliminating expensive joins** by maintaining incremental counters and flags rather than scanning 180 days of data

3. **Using 180-day rolling windows** with increment/decrement logic to maintain accurate sliding window metrics

4. **Materializing final aggregations** in a dashboard metrics table for <10ms query latency

5. **Maintaining full audit history** via SCD Type 2 versioning

The result is a system that provides realtime (<1 minute fresh) dashboard metrics with 360,000x faster query performance and 50% lower costs compared to the daily batch job.
