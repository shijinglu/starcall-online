
# Show Case 1 (Level 1):
* Single App, non-distributed, no middleware

You will be given data folders like
```
data/
    ko_ods_market_rate/
        pt=20260303/data.parquet
        pt=20260304/data.parquet

    dim_user_crypto_withdraw_stats/
        pt=20260303/data.parquet
        pt=20260304/data.parquet
    ...
output/
    result.parquet 
```

Your tasks is to rebuild `dws_okx_risk_deposit_withdraw_aggr_df` SQL logic with pure rust, the final run will be something like

```bash
./single-app --data-dir data/ --output output/result.parquet
```


# Show Case 1 (Level 2):
* Non-distributed, no middleware, realtime, output temporal dimension tables

Same input as above. The tasks are:
1. build a rust service that can process realtime streams
2. CLI to read fact tables and replay the traffic to simulate realtime streams 

```bash
# start the application
./single-service --out-dir output/

# concurrently replay events
for fp in `find data/ -name '*.parquet'`; do
    ./replay --input $fp --topic $fp > logs/$fp.log 2>&1 &
end
```

# Show Case 1 (Level 3):
* Non-distributed, configurable, realtime, output temporal dimension tables

Same input as above. The tasks are:
1. build a rust service that can dynamically load and execute processors
2. Deployable binaries that can execute jobs based on a global configuration (something like global DAG)
3. CLI to read fact tables and replay the traffic to simulate realtime streams 

Pluggable runners are in shared lib format, something like:
```
runners/
    ko_ods_market_rate.so
    dim_user_crypto_withdraw_stats.so
```

```bash
# start the application
./base-runner --out-dir output/

# deploy tasks
./tasks-deployer --runners runners/ --config global-dag.yaml

# concurrently replay events
for fp in `find data/ -name '*.parquet'`; do
    ./replay --input $fp --topic $fp > logs/$fp.log 2>&1 &
end
```

# Show Case 1 (Level 4):
> * **Distributed, configurable, realtime, output temporal dimension tables**

This time the data folder will live in the cloud, like S3 or OSS
```
s3://risk-eu-data/input/
    ko_ods_market_rate/
        pt=20260303/data.parquet
        pt=20260304/data.parquet

    dim_user_crypto_withdraw_stats/
        pt=20260303/data.parquet
        pt=20260304/data.parquet
    ...
s3://risk-eu-data/output/
    result.parquet 
```

Services and runners will be managed by Kubernetes

```bash
# start the application
kubectl deploy --input base-runner.yaml

# deploy tasks
./tasks-deployer --runners runners/ --config global-dag.yaml

# concurrently replay events
./replay --input-s3-bucket s3://risk-eu-data/input
```
