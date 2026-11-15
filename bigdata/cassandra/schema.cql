CREATE KEYSPACE IF NOT EXISTS spotwise
WITH REPLICATION = {'class': 'SimpleStrategy', 'replication_factor': 1};

CREATE TABLE IF NOT EXISTS spotwise.area_signal_summary (
    city text,
    category text,
    metric text,
    area text,
    unit text,
    record_count bigint,
    average_value double,
    median_value double,
    total_value double,
    minimum_value double,
    maximum_value double,
    refreshed_at timestamp,
    PRIMARY KEY ((city, category), metric, area, unit)
);
