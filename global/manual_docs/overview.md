## The idea

In the AI era, do you still need SQL? 

SQL is an expressive language, it translates near natural language description of an data request into executable data plan. But the execution plan is not always optimum. So basically it is the middle man. In the AI era, we potentially can get rid of the middle layer entirely and directly translate natural language data request into low level execution plan with max performance gains.

Both microservice and SQL execution engine is able to fullfil the data transformation request. The reason why SQL is more used in data request is because writing service code for a data request is too heavy and impractical. But in the AI era, this is no longer an issue. 

SQL is a great language to quick validating your idea, but it is bad choice as the building blocks for your data warehouse. It often involves duplicated computation logic. One often compromise the convinience for performance. Compare to highly optmized service logic, each SQL execution may cost a little more, but accumulately, the cost can pile up and start chewing the profit of a company.

Can SQL be optimized? 

The answer is definitly yes

## Past Success Stories:
### Amazon: Spark to Ray — $120M/year saved
https://aws.amazon.com/blogs/opensource/amazons-exabyte-scale-migration-from-apache-spark-to-ray-on-amazon-ec2/
Amazon's Business Data migrated their Spark Jobs (SQL Engine) to Ray (distributed microservices). This translates to saving over 220,000 years of EC2 vCPU computing time, or over $120 million per year on EC2 on-demand R5 instance charges. 

The key optimization wasn't SQL rewriting — it was recognizing that Spark's general-purpose overhead (JVM, shuffle framework, full SQL engine) was unnecessary for what was fundamentally a merge/deduplication workload. Ray let them write custom, tight compaction logic in Python that ran far more efficiently.

### Uber: Custom Remote Shuffle Service (RSS) — saved $2.4M/year
https://liveramp.com/blog/reducing-data-processing-costs-with-ubers-rss
Rather than accepting Spark's built-in local shuffle, Uber built a dedicated Remote Shuffle Service from scratch. Engineers could offload 9-10 PB of disk writes, increasing disk wear-out time from 3 to 36 months, while achieving reliability above 99.99%. InfoQ
The impact propagated beyond Uber. LiveRamp adopted Uber's open-source RSS and saved $2.4 million a year by decoupling shuffle operations ,from compute nodes and introducing a dedicated shuffle cluster — simply by using the basic configuration.

### Snapchat: CPU-only Spark ETL to NVIDIA cuDF to Spark Applications - over 76% in cost savings
https://eng.snap.com/snap-nvidia-gcp
Recent reports saying that Snapchat (Snap Inc.) has achieved significant cost savings, ranging from 76% to 90%, by migrating its Apache Spark ETL (Extract, Transform, Load) and data processing pipelines from CPU-only infrastructure to GPU-accelerated workloads on Google Cloud.

### Notion AI: 90% Cost Reduction by Moving from Spark to Ray
https://www.zenml.io/llmops-database/scaling-vector-search-infrastructure-for-ai-powered-workspace-search
By consolidating data prep and ML inference onto a single compute layer, and eliminating the network hops to third-party APIs, Notion scaled their capacity by 10x while anticipating a 90% reduction in embeddings infrastructure costs.


# The proposal
We are going to translate offline SQL ETL jobs and offline data warehouse operations into a realtime data stream processing system. 

Overall Description:

It will be distributed event based processing systems. 
- there will be many “subscribers” each subscribing to different events. For example, when a user login, make a withdrawal, make a deposit etc. 
- the process consumes the data in the published message, and it is also able to enrich data from different KV tables, for example given the user_id, the process can query from the user dimension table to get basic user information like name, email etc.
- after the processing is completed, the process emits an event notifying that this process is done. And other processors can consume the event and continue processing.
- the distributed DAG will be managed by a central system to prevent cyclical dependency.

Table Design:
This is the core of the design, which is maintaining a list of high performance KV tables.

1. All IDs must use uint64 , there will be an ID service converting the string IDs, or UUIDs into uint64 IDs.
2. All time must be using 13 epoch time format. Additional columns can be used to convert epoch time to other formats like Date, DateTime etc. 
3. All tables are designed for fast lookup, we are going to support mainly two types of tables. 
    2.1 SCD Type 2 tables. For example, the following two tables okx_s2dim_user_basic_info is a SCD Type 2 table, with valid time range. 
    2.2 SCD Type 1 tables. For okx_s1dim_user_first_funnel is a SCD Type 1 table, because the data in the table, by definition, won't change once set. 
    2.3 Partitioned tables. .okx_dim_user_basic_info_df below is a partition table. It also contains history, can allows fast lookup via combined key like (u64id, pt)


3. One entity can have many dimension tables, for example a user can have a table for slow changing properties like name, email etc. It can also have a fat changing dim table for tracking properties “cnt_homepage_views”, “cnt_manual_price_refreshes” etc. 

```
.okx_s2dim_user_basic_info
    - u64id
    - name
    - email
    - phone
    - valid_from
    - valid_to
.okx_s1dim_user_first_funnel
    - u64id
    - signup_channel
    - first_kyc_pass_time
    - first_trade_time 
    - first_trade_amount
    - Updated_at
.okx_dim_user_basic_info_df
    - u64id
    - kyc_name
    - kyc_level
    - total_asset
    - pt
```

Some pipeline principals:
1. Unless explicitly stated, all processes must be idempotent for better support of traffic replay.
2. All events must include an idempotent key for dedupe. 




The system supports converting offline ETL jobs into realtime pipelines, following is how it works:

Given an offline SQL, which can be very complicated. In the attached example, the ETL job is ex_offline.dws_okx_risk_deposit_withdraw_aggr_df. This job takes about 1 hour to finish, which costs about ~36 USD on databricks platform.

But we believe it can be optimized by making it realtime. Then it comes to the critical step:

This step basically breaks the offline batch SQL job into something realtime or near realtime by pre-computing the state into SCD Type 2 dimension tables, aka the performant KV tables. Each dim table needs to capture the evolving state of  entities’ metrics so that at any point in time, one can look up the entity attributes based on entity ID and the event time.

In the dws_okx_risk_deposit_withdraw_aggr_df example, we refactored the original data pipeline into several steps with intermediate KV tables:
- dim_market_rates: Changes when: an exchange rate changes
- dim_user_crypto_deposit_stats : any deposit order is created or status changes
- ...

