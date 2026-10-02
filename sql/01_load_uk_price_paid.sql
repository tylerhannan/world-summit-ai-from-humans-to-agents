-- From https://clickhouse.com/docs/get-started/sample-datasets/uk-price-paid
-- About 27M rows. The insert takes a minute or two on ClickHouse Cloud.
CREATE DATABASE IF NOT EXISTS uk;

CREATE TABLE IF NOT EXISTS uk.uk_price_paid
(
  price UInt32,
  date Date,
  postcode1 LowCardinality(String),
  postcode2 LowCardinality(String),
  type Enum8('terraced' = 1, 'semi-detached' = 2, 'detached' = 3, 'flat' = 4, 'other' = 0),
  is_new UInt8,
  duration Enum8('freehold' = 1, 'leasehold' = 2, 'unknown' = 0),
  addr1 String,
  addr2 String,
  street LowCardinality(String),
  locality LowCardinality(String),
  town LowCardinality(String),
  district LowCardinality(String),
  county LowCardinality(String)
)
ENGINE = MergeTree
ORDER BY (postcode1, postcode2, addr1, addr2);

INSERT INTO uk.uk_price_paid
SELECT
  toUInt32(price_string) AS price,
  parseDateTimeBestEffortUS(time) AS date,
  splitByChar(' ', postcode)[1] AS postcode1,
  splitByChar(' ', postcode)[2] AS postcode2,
  transform(a, ['T', 'S', 'D', 'F', 'O'], ['terraced', 'semi-detached', 'detached', 'flat', 'other']) AS type,
  b = 'Y' AS is_new,
  transform(c, ['F', 'L', 'U'], ['freehold', 'leasehold', 'unknown']) AS duration,
  addr1, addr2, street, locality, town, district, county
FROM url(
  'http://prod1.publicdata.landregistry.gov.uk.s3-website-eu-west-1.amazonaws.com/pp-complete.csv',
  'CSV',
  'uuid_string String, price_string String, time String, postcode String, a String, b String, c String,
   addr1 String, addr2 String, street String, locality String, town String, district String, county String,
   d String, e String'
) SETTINGS max_http_get_redirects = 10;

SELECT count() FROM uk.uk_price_paid;
