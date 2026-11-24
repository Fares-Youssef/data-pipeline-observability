-- Azure Synapse Analytics - serverless SQL pool over the DEPI data lake (Module 7.2 / 7.3).
-- Run in Synapse Studio > Develop > SQL script, connected to "Built-in".
-- Replace __STORAGE_ACCOUNT__ with the ADLS Gen2 account name. The workspace managed identity
-- needs the "Storage Blob Data Reader" role on that account.

IF DB_ID('depi_lake') IS NULL CREATE DATABASE depi_lake COLLATE Latin1_General_100_BIN2_UTF8;
GO
USE depi_lake;
GO
IF NOT EXISTS (SELECT * FROM sys.database_scoped_credentials WHERE name = 'WorkspaceIdentity')
    CREATE DATABASE SCOPED CREDENTIAL WorkspaceIdentity WITH IDENTITY = 'Managed Identity';
GO
IF NOT EXISTS (SELECT * FROM sys.external_data_sources WHERE name = 'depi_lake_ds')
    CREATE EXTERNAL DATA SOURCE depi_lake_ds
    WITH (LOCATION = 'https://__STORAGE_ACCOUNT__.dfs.core.windows.net/lake', CREDENTIAL = WorkspaceIdentity);
GO
CREATE OR ALTER VIEW dbo.dim_location AS
SELECT * FROM OPENROWSET(BULK 'silver/dim_location/*.parquet', DATA_SOURCE = 'depi_lake_ds', FORMAT = 'PARQUET') AS r;
GO
CREATE OR ALTER VIEW dbo.dim_model AS
SELECT * FROM OPENROWSET(BULK 'silver/dim_model/*.parquet', DATA_SOURCE = 'depi_lake_ds', FORMAT = 'PARQUET') AS r;
GO
CREATE OR ALTER VIEW dbo.fact_forecast AS
SELECT r.*, CAST(r.filepath(1) AS date) AS issue_date
FROM OPENROWSET(BULK 'silver/fact_forecast/issue_date=*/*.parquet', DATA_SOURCE = 'depi_lake_ds', FORMAT = 'PARQUET') AS r;
GO
CREATE OR ALTER VIEW dbo.fact_observation AS
SELECT r.*, r.filepath(1) AS observation_month
FROM OPENROWSET(BULK 'silver/fact_observation/observation_month=*/*.parquet', DATA_SOURCE = 'depi_lake_ds', FORMAT = 'PARQUET') AS r;
GO
CREATE OR ALTER VIEW dbo.model_accuracy AS
SELECT * FROM OPENROWSET(BULK 'gold/model_accuracy/*.parquet', DATA_SOURCE = 'depi_lake_ds', FORMAT = 'PARQUET') AS r;
GO
CREATE OR ALTER VIEW dbo.model_disagreement AS
SELECT r.*, CAST(r.filepath(1) AS date) AS issue_date
FROM OPENROWSET(BULK 'gold/model_disagreement/issue_date=*/*.parquet', DATA_SOURCE = 'depi_lake_ds', FORMAT = 'PARQUET') AS r;
GO
