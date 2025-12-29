defmodule DbPortal.AthenaTools.AthenaAws do
  require Logger
  alias DbPortal.DbMetadata.Aws
  alias Porcelain


  def get_athena_catalog_db_names() do
    ["ab_tests", "alchemy", "amplitude", "answer_review", "asset_tags", "async_events",
    "authoring_solution", "backup", "categorization", "cdws_analytics", "cdws_content",
    "cdws_bronze", "cdws_silver", "cdws_gold",
    "cdws_datamarts", "cdws_datawarehouse", "cdws_dev_intelligent_uploader",
    "cdws_di", "cdws_esx", "cdws_finance", "cdws_dataeng", "cdws_intelligent_uploader", "cdws_ml",
    "cdws_seo", "cdws_stats", "cdws_symbolab", "cdws_tutors", "content_fingerprint", "converter",
      "copyright", "course_hero",
      "cdws_bronze_stg", "cdws_silver_stg", "cdws_gold_stg",
      "cdws_marketing",
      "csbx_dataeng", "csbx_bronze", "csbx_silver", "csbx_gold", "csbx_di",
      "csbx_gmulgaonkar", "csbx_rliebling","csbx_vmendi", "csbx_aawasthi",
      "csbx_ktiwana","csbx_ktiwana_stg","csbx_ktiwana_test", "csbx_jpang",
      "csbx_finance","csbx_seo",
    "csbx_staging", "csbx_rel", "csdx_analytics", "csbx_tutors",
    "dbadmin", "default",
    "dev_ab_tests", "dev_asset_tags", "dev_async_events", "dev_course_hero",
    "dev_event_tracking", "dev_fcpress", "dev_question_identifier", "dev_search",
    "dev_splitter", "document_performance_prediction", "event_tracking", "experts",
    "fcpress", "funnel", "geoip", "jjia_test", "logs", "ml_services", "payment",
    "question_identifier", "quiz_assessment_tool", "recycle_bin", "recycle_bin_temp", "search", "splitter",
    "study_guides", "tempdb", "textbooks", "tutor_dashboard", "user_agent",
      "zendesk"]
      |> Enum.sort()
    # result = get_athena_catalog_db_list()
    # case result do
    #   {:ok, database_list} -> Enum.map(database_list, fn db -> db["Name"] end)
    #   {:error, _} -> []
    # end
  end

  def get_athena_tables!(database_name, next_token \\ nil, aws_client \\ Aws.aws_client()) do
    opts = %{
      :CatalogName => "AwsDataCatalog",
      :DatabaseName => database_name,
      :NextToken => next_token,
      :MaxResults => 100
    }
    case AWS.Glue.get_tables(aws_client, opts) do
      {:ok, parsed_body, _response} ->
        tables = Enum.map(parsed_body["TableList"],
            fn table ->
              %{table_name: table["Name"],
                database_name: table["DatabaseName"],
                location: table["StorageDescriptor"]["Location"],
                created_by: table["CreatedBy"],
                is_view: table["TableType"] == "VIRTUAL_VIEW"
              }
            end
          )
        {:ok, tables, parsed_body["NextToken"]}
      {:error, response} ->
        raise "get_athena_tables error: #{inspect response}"
    end
  end

  def get_athena_table!(table, aws_client \\ Aws.aws_client()) do
    opts = %{
      :CatalogName => "AwsDataCatalog",
      :DatabaseName => table.database_name,
      :Name => table.table_name
    }
    case AWS.Glue.get_table(aws_client, opts) do
      {:ok, parsed_body, _} ->
        %{
          table_name: parsed_body["Table"]["Name"],
          database_name: parsed_body["Table"]["DatabaseName"],
          location: parsed_body["Table"]["StorageDescriptor"]["Location"],
          created_by: parsed_body["Table"]["CreatedBy"],
          is_view: parsed_body["Table"]["TableType"] == "VIRTUAL_VIEW"
        }
      {:error, response} ->
        raise "get_athena_table error: Couldn't fetch table #{encode!(table)}, response #{inspect response}"
    end
  end

  def athena_table_exists?(table, aws_client \\ Aws.aws_client()) do
    opts = %{
      :CatalogName => "AwsDataCatalog",
      :DatabaseName => table.database_name,
      :Name => table.table_name
    }
    case AWS.Glue.get_table(aws_client, opts) do
      {:ok, _parsed_body, _} ->
        true
      {:error, {:unexpected_response, parsed_body}} ->
        if String.contains?(parsed_body.body, "EntityNotFoundException") do
          false
        else
          raise "athena_table_exists error: Unexpected response #{parsed_body}"
        end
    end
  end


  def athena_database_exists?(database_name, aws_client \\ Aws.aws_client()) do
    opts = %{
      :CatalogName => "AwsDataCatalog",
      :Name => database_name
    }
    case AWS.Glue.get_database(aws_client, opts) do
      {:ok, _parsed_body, _} ->
        true
      {:error, {:unexpected_response, parsed_body}} ->
        if String.contains?(parsed_body.body, "EntityNotFoundException") do
          false
        else
          raise "athena_database_exists error: Unexpected response #{parsed_body}"
        end
    end
  end


  def copy_table!(src_table, dst_table, aws_client \\ Aws.aws_client(), logger \\ &Logger.log/2) do
    logger.(:info, "copy_table copying: #{encode!(src_table)} => #{encode!(dst_table)}")

    if src_table.database_name == dst_table.database_name and
       src_table.table_name == dst_table.table_name and
       src_table.location != dst_table.location do
        alter_table_location!(src_table, dst_table.location, "cp", aws_client, logger)
    else
      copy_or_move_table!(src_table, dst_table, "cp", aws_client, logger)
    end
  end

  def move_table!(src_table, dst_table, aws_client \\ Aws.aws_client(), logger \\ &Logger.log/2) do
    logger.(:info, "move_table moving: #{encode!(src_table)} => #{encode!(dst_table)}")


    if src_table.database_name == dst_table.database_name and
       src_table.table_name == dst_table.table_name and
       src_table.location != dst_table.location do
        alter_table_location!(src_table, dst_table.location, "mv", aws_client, logger)
    else
      copy_or_move_table!(src_table, dst_table, "mv", aws_client, logger)
    end
  end


  def move_to_recycle_bin!(src_table, aws_client \\ Aws.aws_client(), logger \\ &Logger.log/2) do
    logger.(:info, "move_to_recycle_bin recycling: #{encode!(src_table)}")

    dst_table = %{
      :database_name => "recycle_bin",
      :table_name => src_table.table_name,
      :location => "s3://ch-dws/sandbox/recycle_bin/#{src_table.table_name}/"   # It could be the last path fragment of the src location instead
    }

    copy_or_move_table!(src_table, dst_table, "mv", aws_client, logger)
  end


  def permanently_delete_allowed?(database_name) do
    database_name in ["recycle_bin", "recycle_bin_temp", "tempdb"]
    or
    String.starts_with?(database_name, "csbx_")
    or
    String.starts_with?(database_name, "cdws_bronze")
    or
    String.starts_with?(database_name, "cdws_silver")
    or
    String.starts_with?(database_name, "cdws_gold")
  end

  def permanently_delete_table_allowed?(database_name, location) do
    permanently_delete_allowed?(database_name)
    and
    Enum.any?(["s3://ch-dws/sandbox/",
               "s3://ch-sbx-data-sqoop/sandbox_db/",
               "s3://ch-sbx-data-sqoop/tempdb/",
               "s3://aws-athena-query-results-315915642113-us-east-1/",
               "s3://ch-ml-data/doc_recommendations_v2/",
               "s3://ch-dws/prod/cdws_bronze",
               "s3://ch-dws/prod/cdws_silver",
               "s3://ch-dws/prod/cdws_gold"],
               &(String.starts_with?(location, &1) and String.length(location) > String.length(&1) + 2))
  end

  def permanently_delete_view_allowed?(database_name) do
    permanently_delete_allowed?(database_name)
  end

  def delete_table_permanently!(src_table, aws_client \\ Aws.aws_client(), logger \\ &Logger.log/2) do
    logger.(:info, "delete_table_permanently deleting: #{encode!(src_table)}")

    src_table = get_athena_table!(src_table, aws_client)
    src_table = Map.put(src_table, :location, sanitize_location(src_table.location))

    if src_table.is_view do
      if not permanently_delete_view_allowed?(src_table.database_name) do
        raise "Can't permanently delete view #{encode!(src_table)}"
      end
    else
      if not permanently_delete_table_allowed?(src_table.database_name, src_table.location) do
        raise "Can't permanently delete table #{encode!(src_table)}"
      end
    end

    delete_glue_table!(src_table, aws_client, logger)
    if not src_table.is_view do
      delete_s3_location!(src_table.location, aws_client, logger)
    end
  end


  def create_or_replace_view!(view_info, aws_client \\ Aws.aws_client(), logger \\ &Logger.log/2) do
    logger.(:info, "create_or_replace_view, #{view_info.database_name}.#{view_info.view_name}")

    query_text = "CREATE OR REPLACE VIEW #{view_info.database_name}.#{view_info.view_name} AS #{view_info.select_query}"
    case execute_query(query_text, view_info.database_name, [], aws_client, logger) do
      {:ok} ->
        logger.(:info, "create_or_replace_view ok: query execution succeeded for #{encode!(view_info)}")
        {:ok}
      {:error, error_msg} ->
        logger.(:error, "create_or_replace_view error with message: query execution failed for #{encode!(view_info)}")
        {:error, error_msg}
      _ ->
        raise "create_or_replace_view unknown error: query execution failed for #{encode!(view_info)}"
    end
  end


  defp alter_table_location!(src_table, dst_location, operation, aws_client, logger) do
    logger.(:info, "alter_table_location: operation #{operation}, #{encode!(src_table)} => dst_location #{dst_location}")

    # Query Athena to ensure we have the right location for the old table
    src_table = get_athena_table!(src_table, aws_client)

    # Make sure locations end with trailing slash
    src_table = Map.put(src_table, :location, sanitize_location(src_table.location))
    dst_location = sanitize_location(dst_location)

    # Detect early that destination location can't be the same as source location.
    if src_table.location == dst_location do
      raise "alter_table_location error: Source location #{src_table.location} can't be the same as Destination location #{dst_location}"
    end

    # Don't copy/move the table location to an already existing location. Destinations are exclusive for single tables.
    case s3_folder_exists(dst_location, aws_client) do
      {:ok, false} ->
        logger.(:info, "alter_table_location ok: destination #{dst_location} doesn't exist")
      {:ok, true} ->
        raise "alter_table_location error: S3 destination #{dst_location} already exists"
      {:error, error_msg} ->
        raise "alter_table_location error: S3 destination #{dst_location}, error_msg #{inspect error_msg}"
    end

    # Move S3 data to new location. Make sure there's something to copy or move first.
    case s3_folder_exists(src_table.location, aws_client) do
      {:ok, true} ->
        s3_copy_or_move!(src_table.location, dst_location, operation, logger)
      {:ok, false} ->
        logger.(:info, "alter_table_location source #{src_table.location} doesn't exist, there's no data to copy or move")
      {:error, error_msg} ->
        raise "alter_table_location error: S3 source #{src_table.location}, error_msg #{inspect error_msg}"
    end

    # Finally, make the table point to the new location
    alter_table_set_location!(src_table, dst_location, aws_client, logger)
  end


  defp copy_or_move_table!(src_table, dst_table, operation, aws_client, logger) do
    logger.(:info, "copy_or_move_table performing operation #{operation}, source #{encode!(src_table)}, destination #{encode!(dst_table)}")

    # Query Athena to ensure we have the right location for the old table
    src_table = get_athena_table!(src_table, aws_client)

    # Make sure locations end with trailing slash
    src_table = Map.put(src_table, :location, sanitize_location(src_table.location))
    dst_table = Map.put(dst_table, :location, sanitize_location(dst_table.location))

    # Destination database must me precreated
    if not athena_database_exists?(dst_table.database_name) do
      raise "copy_or_move_table error: Destination database must exist #{encode!(dst_table)}"
    end

    # Destination table can't exist yet
    if athena_table_exists?(dst_table) do
      raise "copy_or_move_table error: Destination table already exists #{encode!(dst_table)}"
    end

    # Don't copy the table to an already existing location. Destinations are exclusive for single tables.
    if src_table.location != dst_table.location do
      case s3_folder_exists(dst_table.location, aws_client) do
        {:ok, false} ->
          logger.(:info, "copy_or_move_table ok: destination #{dst_table.location} doesn't exist")
        {:ok, true} ->
          raise "copy_or_move_table error: S3 destination #{dst_table.location} already exists"
        {:error, error_msg} ->
          raise "copy_or_move_table error: S3 destination #{dst_table.location}, error_msg #{inspect error_msg}"
      end
    end

    # Create new Glue table pointing to database and location
    copy_glue_table!(src_table, dst_table, aws_client, logger)

    # Copy or move S3 data to new location. Make sure there's something to copy or move first.
    if src_table.location != dst_table.location do
      case s3_folder_exists(src_table.location, aws_client) do
        {:ok, true} ->
          s3_copy_or_move!(src_table.location, dst_table.location, operation, logger)
        {:ok, false} ->
          logger.(:info, "copy_or_move_table source #{src_table.location} doesn't exist, there's no data to copy or move")
        {:error, error_msg} ->
          raise "copy_or_move_table error: S3 source #{src_table.location}, error_msg #{inspect error_msg}"
      end
    end

    # Force the Glue catalog to read partition metadata from S3
    msck_repair_table!(dst_table, aws_client, logger)

    if operation == "mv" do
      # Delete source table from the Glue catalog
      delete_glue_table!(src_table, aws_client, logger)
    end

    {:ok}
  end


  defp s3_copy_or_move!(src_location, dst_location, s3_operation, logger) do
    logger.(:info, "s3_copy_or_move #{s3_operation} table from #{src_location} to #{dst_location}")

    if not Enum.all?([src_location, dst_location], &(String.ends_with?(&1, "/"))) do
      raise "s3_copy_or_move error: Locations have to end with trailing slash, source #{src_location}, destination #{dst_location}"
    end

    logger.(:info, "aws s3 #{s3_operation} #{src_location} #{dst_location} --recursive")
    res = Porcelain.exec("aws", ["s3", s3_operation, src_location, dst_location, "--recursive"])

    # TODO: Handle log output asynchronously
    case res do
      %{out: output, status: 0} ->
        logger.(:info, "s3_copy_or_move succeeded")
        {:ok, log: output}
      %{out: output, status: error_code, err: error} ->
        raise "s3_copy_or_move error: output #{output}, error_code #{error_code}, error: #{error}"
    end
  end


  defp copy_glue_table!(athena_src_table, athena_dst_table, aws_client, logger) do
    logger.(:info, "copy_glue_table copying table from #{encode!(athena_src_table)} to #{encode!(athena_dst_table)}")

    src_table_opts = %{
      :CatalogName => "AwsDataCatalog",
      :DatabaseName => athena_src_table.database_name,
      :Name => athena_src_table.table_name
    }

    case AWS.Glue.get_table(aws_client, src_table_opts) do
      {:ok, parsed_body, _response} ->
        src = parsed_body["Table"]
        src = put_in(src["StorageDescriptor"]["Location"], athena_dst_table.location)
        src = if src["StorageDescriptor"]["SerdeInfo"] do
          put_in(src["StorageDescriptor"]["SerdeInfo"]["Name"], athena_dst_table.table_name)
        else
          src
        end

        dst_table_opts = %{
          CatalogId: src["CatalogId"],
          DatabaseName: athena_dst_table.database_name,
          TableInput: %{
            Name: athena_dst_table.table_name,
            Description: src["Description"],
            Owner: src["Owner"],
            Retention: src["Retention"],
            StorageDescriptor: src["StorageDescriptor"],
            PartitionKeys: src["PartitionKeys"],
            ViewOriginalText: src["ViewOriginalText"],
            ViewExpandedText: src["ViewExpandedText"],
            Parameters: src["Parameters"],
            TableType: src["TableType"]
          }
        }
        case AWS.Glue.create_table(aws_client, dst_table_opts) do
          {:ok, _parsed_body, _response} ->
            {:ok}
          {:error, response} ->
            raise "copy_glue_table error: Couldn't create table #{encode!(athena_dst_table)}, response #{inspect response}"
        end
      {:error, response} ->
        raise "copy_glue_table error: Couldn't get table #{encode!(athena_src_table)}, response #{inspect response}"
    end
  end


  defp delete_glue_table!(athena_table, aws_client, logger) do
    logger.(:info, "delete_glue_table deleting #{encode!(athena_table)}")
    opts = %{
      :CatalogName => "AwsDataCatalog",
      :DatabaseName => athena_table.database_name,
      :Name => athena_table.table_name
    }
    case AWS.Glue.delete_table(aws_client, opts) do
      {:ok, _parsed_body, _response} -> {:ok}
      {:error, response} -> raise "delete_glue_table error: #{inspect response}"
    end
  end


  defp delete_s3_location!(location, _aws_client, logger) do
    logger.(:info, "delete_s3_location from #{location}")

    if not String.ends_with?(location, "/") do
      raise "delete_s3_location error: Locations have to end with trailing slash"
    end

    logger.(:info, "aws s3 rm #{location} --recursive")
    res = Porcelain.exec("aws", ["s3", "rm", location, "--recursive"])

    # TODO: Handle log output asynchronously
    case res do
      %{out: output, status: 0} ->
        logger.(:info, "delete_s3_location succeeded")
        {:ok, log: output}
      %{out: output, status: error_code, err: error} ->
        raise "delete_s3_location error: output #{output}, error_code #{error_code}, error: #{error}"
    end
  end


  defp alter_table_set_location!(athena_table, dst_location, aws_client, logger) do
    logger.(:info, "alter_table_set_location, #{athena_table.location} => #{dst_location}")

    query_text = "ALTER TABLE #{athena_table.database_name}.#{athena_table.table_name} SET LOCATION '#{dst_location}'"
    case execute_query(query_text, athena_table.database_name, [], aws_client, logger) do
      {:ok} ->
        logger.(:info, "alter_table_set_location ok: query execution succeeded for table #{encode!(athena_table)}")
        {:ok}
      _ ->
        raise "alter_table_set_location error: query execution failed for table #{encode!(athena_table)}"
    end
  end


  defp msck_repair_table!(athena_table, aws_client, logger) do
    logger.(:info, "msck_repair_table #{encode!(athena_table)}")

    query_text = "MSCK REPAIR TABLE #{athena_table.database_name}.#{athena_table.table_name}"
    case execute_query(query_text, athena_table.database_name, [], aws_client, logger) do
      {:ok} ->
        logger.(:info, "msck_repair_table ok: query execution succeeded for table #{encode!(athena_table)}")
        {:ok}
      _ ->
        raise "msck_repair_table error: query execution failed for table #{encode!(athena_table)}"
    end
  end


  @default_query_location "s3://aws-athena-query-results-315915642113-us-east-1/"
  @default_workgroup "secondary"

  def execute_query(query_text, database_name, options \\ [], aws_client \\ Aws.aws_client(), logger \\ &Logger.log/2) do
    default_options = [output_location: @default_query_location, workgroup: @default_workgroup]
    options = Keyword.merge(default_options, options) |> Enum.into(%{})
    %{output_location: output_location, workgroup: workgroup} = options

    logger.(:info, "execute_query #{query_text}, database_name #{database_name}, output_location #{output_location}, workgroup #{workgroup}")
    start_query_execution_input = %{
      :QueryString => query_text,
      :QueryExecutionContext => %{Database: database_name},
      :ResultConfiguration => %{OutputLocation: output_location},
      :ClientRequestToken => :crypto.hash(:md5, to_string(DateTime.now!("Etc/UTC"))) |> Base.encode16(),
      :WorkGroup => workgroup
    }

    case AWS.Athena.start_query_execution(aws_client, start_query_execution_input) do
      {:ok, parsed_body, _response} ->
        wait_for_query_finished(%{:QueryExecutionId => parsed_body["QueryExecutionId"]}, 60*60, aws_client, logger)

      {:error, response} ->
        error_msg = "execute_query error: start_query_execution failed, response #{inspect response}"
        logger.(:error, error_msg)
        {:error, error_msg}
    end
  end


  @query_failure_states ["FAILED", "CANCELED"]
  @query_intermediate_states ["QUEUED", "RUNNING"]
  @time_between_status_checks_seconds 10

  defp wait_for_query_finished(query_execution_id, timeout, _aws_client, logger) when timeout <= 0, do:
    logger.(:error, "wait_for_query_finished error: query #{inspect query_execution_id} timed out, timeout #{timeout}")
    {:error, "query timed out"}


  defp wait_for_query_finished(query_execution_id, timeout, aws_client, logger) do
    case AWS.Athena.get_query_execution(aws_client, query_execution_id) do
      {:ok, parsed_body, _response} ->
        query_status = parsed_body["QueryExecution"]["Status"]["State"]
        cond do
          query_status in @query_intermediate_states ->
            logger.(:info, "wait_for_query_finished loop: query #{inspect query_execution_id} in state #{query_status}")
            :timer.sleep(@time_between_status_checks_seconds * 1000);
            wait_for_query_finished(query_execution_id, timeout - @time_between_status_checks_seconds, aws_client, logger)

          query_status in @query_failure_states ->
            error_msg = "wait_for_query_finished error: query #{inspect query_execution_id} failed, parsed_body #{encode!(parsed_body)}"
            logger.(:error, error_msg)
            {:error, error_msg}

          true ->
            logger.(:info, "wait_for_query_finished succeeded: query #{inspect query_execution_id} in state #{query_status}")
            {:ok}
        end

      {:error, response} ->
        error_msg = "wait_for_query_finished error: query #{inspect query_execution_id} failed, response #{inspect response}"
        logger.(:error, error_msg)
        {:error, error_msg}
    end
  end


  def s3_folder_exists(location, aws_client \\ Aws.aws_client()) do
    {bucket, prefix} = s3_parse_url(location)

    prefix = String.trim_leading(prefix, "/")
    case AWS.S3.list_objects(aws_client, bucket, nil, nil, nil, 1, prefix) do
      {:ok, parsed_body, %{status_code: 200}} ->
        {:ok, get_in(parsed_body, ["ListBucketResult", "Contents"]) != nil}
      {:error, response} ->
        {:error, error_msg: "s3_folder_exists error: #{inspect response}"}
    end
  end


  def s3_parse_url(s3_url) do
    result = URI.parse(s3_url)
    {result.host, result.path}
  end

  defp encode!(something) do
    Jason.encode!(something, pretty: true)
  end


  defp sanitize_location(location) do
    if not String.ends_with?(location, "/"), do: location <> "/", else: location
  end

end
