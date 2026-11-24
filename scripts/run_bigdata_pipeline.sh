#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "Usage: $0 <standardized.csv|standardized.parquet> <category> [metric]" >&2
  exit 2
fi

input_file="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
category="$2"
metric="${3:-value}"
if [[ ! -f "$input_file" ]]; then
  echo "Input file not found: $input_file" >&2
  exit 2
fi

case "${input_file##*.}" in
  csv) input_format=csv ;;
  parquet) input_format=parquet ;;
  *) echo "Input must be a CSV or Parquet file." >&2; exit 2 ;;
esac

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required. Install Docker Desktop or Docker Engine and Compose v2." >&2
  exit 2
fi

run_id="$(date -u +%Y%m%dT%H%M%SZ)-$$"
input_dir="/spotwise/raw/${run_id}"
output_dir="/spotwise/curated/${run_id}"
input_name="$(basename "$input_file")"
input_parent="$(dirname "$input_file")"

docker compose --profile bigdata up -d spotwise hdfs-namenode hdfs-datanode mongodb cassandra spark-master spark-worker

mongo_collection="raw_${run_id//-/_}"
for attempt in $(seq 1 30); do
  if docker compose --profile bigdata exec -T mongodb mongosh --quiet --eval 'db.adminCommand("ping").ok' 2>/dev/null | grep -q 1; then
    break
  fi
  if [[ "$attempt" -eq 30 ]]; then
    echo "MongoDB did not become ready. Check: docker compose --profile bigdata logs mongodb" >&2
    exit 1
  fi
  sleep 2
done

for attempt in $(seq 1 60); do
  if docker compose --profile bigdata exec -T cassandra cqlsh 127.0.0.1 -e 'DESCRIBE CLUSTER' >/dev/null 2>&1; then
    break
  fi
  if [[ "$attempt" -eq 60 ]]; then
    echo "Cassandra did not become ready. Check: docker compose --profile bigdata logs cassandra" >&2
    exit 1
  fi
  sleep 5
done

docker compose --profile bigdata exec -T cassandra cqlsh 127.0.0.1 -f /opt/spotwise/schema.cql
docker compose --profile bigdata run --rm hdfs-client \
  "for attempt in \$(seq 1 60); do hdfs dfsadmin -report 2>/dev/null | grep -q 'Live datanodes (1)' && hdfs dfs -mkdir -p '${input_dir}' && exit 0; sleep 2; done; echo 'HDFS DataNode did not become ready' >&2; exit 1"
docker compose --profile bigdata run --rm \
  -v "${input_parent}:/spotwise-input:ro" hdfs-client \
  "hdfs dfs -put -f '/spotwise-input/${input_name}' '${input_dir}/${input_name}'"

docker compose --profile bigdata run --rm spark-submit \
  --master spark://spark-master:7077 \
  --deploy-mode client \
  --packages com.datastax.spark:spark-cassandra-connector_2.12:3.5.1,org.mongodb.spark:mongo-spark-connector_2.12:10.7.0 \
  --conf spark.driver.host=spark-submit \
  --conf spark.driver.bindAddress=0.0.0.0 \
  --conf spark.driver.port=7080 \
  --conf spark.blockManager.port=7081 \
  --conf spark.cores.max=2 \
  --conf spark.hadoop.fs.defaultFS=hdfs://namenode:9000 \
  --conf spark.hadoop.dfs.replication=1 \
  --conf spark.cassandra.connection.host=cassandra \
  --conf spark.mongodb.write.connection.uri="mongodb://mongodb:27017/spotwise?retryWrites=true" \
  --conf spark.mongodb.read.connection.uri="mongodb://mongodb:27017/spotwise?retryWrites=true" \
  /opt/spotwise/spark_etl.py \
  --input "hdfs://namenode:9000${input_dir}" \
  --format "$input_format" \
  --category "$category" \
  --metric "$metric" \
  --output "hdfs://namenode:9000${output_dir}" \
  --mongo-uri "mongodb://mongodb:27017/spotwise?retryWrites=true" \
  --mongo-database spotwise \
  --mongo-collection "$mongo_collection"

echo "Pipeline complete. HDFS curated: ${output_dir}; Mongo raw: spotwise.${mongo_collection}; Cassandra summaries: spotwise.area_signal_summary"
