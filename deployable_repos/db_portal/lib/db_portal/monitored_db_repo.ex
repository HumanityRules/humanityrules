defmodule DbPortal.MonitoredDbRepo do
  @moduledoc ~S"""
  Repo module to access the monitored db hosts
  """
  use Ecto.Repo,
    otp_app: :db_portal,
    adapter: Ecto.Adapters.MyXQL,
    pool_size: 15

  def with_dynamic_repo(hostname, schema, callback) do
    credentials = [
      hostname: hostname,
      database: schema,
      readonly: true,
      username: "chdbadmin",
      password: DbPortal.Secrets.value()
    ]

    default_dynamic_repo = get_dynamic_repo()
    start_opts = [name: nil, pool_size: 1] ++ credentials
    {:ok, repo} = start_link(start_opts)

    try do
      put_dynamic_repo(repo)
      callback.()
    after
      put_dynamic_repo(default_dynamic_repo)
      Supervisor.stop(repo)
    end
  end

  @read_only_regex ~r{^\s*(select|show|describe|explain|with)}i
  def exec_ro_query(qry, host, schema) do
    cond do
      # IMPORTANT: using Ecto.Adapters.SQL.stream is to prevent
      # the MyXQL driver from loading an excessive number of rows and
      # exhausting memory.  It ends up doing a prepared statement and using a cursor
      # to limit how much it loads.  However, mysql 8 (some versions at least 8.4.2 in docker)
      # crashes from this.  And, we've seen Aurora v3.04.2 also crash (but not always)
      # Thus, we treat `explains` differently, as they are not at risk of returning ridiculously
      # large numbers of rows
      Regex.match?(~r/\A\s*explain/i, qry) ->
        with_dynamic_repo(host, schema, fn ->
          case Ecto.Adapters.SQL.query(get_dynamic_repo(), qry) do
            {:ok, results} -> {:ok, results} |> dbg()

            {:error, _} = e -> e
          end
        end)

      Regex.match?(@read_only_regex, qry) ->
        with_dynamic_repo(host, schema, fn ->
          qry =
            qry
            |> with_max_execution_time(10_000)

          try do
            case transaction(fn ->
              Ecto.Adapters.SQL.stream(get_dynamic_repo(), qry, [], max_rows: 500)
              |> Enum.take(2)
            end) do
              {:ok, results} -> {:ok, combine_myxql_results(results)}
              {:error, _} = e -> e
            end
          rescue
            e -> {:error, e}
          end
        end)
      true ->
        {:error, "query must match this regex #{inspect(@read_only_regex)}"}
    end
  end

  def combine_myxql_results([single]), do: single

  def combine_myxql_results([h | _tail] = all) do
    %MyXQL.Result{
      h
      | num_rows: all |> Enum.map(& &1.num_rows) |> Enum.sum(),
        rows: all |> Enum.map(& &1.rows) |> Enum.concat()
    }
  end

  @max_exec_re ~r/\ASELECT /i
  def with_max_execution_time(q, max \\ 10_000) do
    Regex.replace(@max_exec_re, q, "SELECT /*+ max_execution_time(#{max}) */ ")
  end

  def sample(host) do
    # {host, mysql_digest, query_text, digest_text, sampled_at}
    qry = ~s"""
      SELECT
        '#{host}' AS host,
        IF(esc.digest != '00000000000000000000000000000000', esc.digest, NULL) AS mysql_digest,
        p.info AS query_text,
        esc.digest_text,
        UTC_TIMESTAMP() AS sampled_at
      FROM
        threads t
      JOIN events_statements_current esc
        ON t.thread_id = esc.thread_id
      JOIN information_schema.processlist p
        ON p.id = t.processlist_id
      WHERE p.info IS NOT NULL
        AND esc.digest_text IS NOT NULL
    """

    with_dynamic_repo(host, "performance_schema", fn ->
      case query(qry, [], log: false) do
        # {:ok, Enum.map(rows, &hd/1)}
        {:ok, %MyXQL.Result{rows: rows}} -> {:ok, rows}
        {:error, e} -> {:error, e}
      end
    end)
  end

  def show_schemas(host, ignored_schemas \\ []) do
    case exec_ro_query("SHOW DATABASES", host, "") do
      {:ok, %MyXQL.Result{rows: schemas}} ->
        schemas = schemas |> Enum.flat_map(& &1)
        schemas -- ignored_schemas

      _err ->
        nil
    end
  end

  def show_create_table(schema, table) do
    q = "SHOW CREATE TABLE #{table}"

    with {rd_endpoint, _wrt_endpoint, schema_name} <-
           DbPortal.DbMetadata.endpoints_and_schema(schema),
         {:ok, result} <-
           DbPortal.MonitoredDbRepo.exec_ro_query(q, rd_endpoint, schema_name) |> IO.inspect(),
         %MyXQL.Result{rows: rows} <- result,
         [[_tbl_name, create_table]] <- rows do
      {:ok, create_table}
    else
      {:error, _e} = err -> err
    end
  end

  def table_status(schema, table) do
    q = "SHOW TABLE STATUS like '#{table}'"
    {rd_endpoint, _wrt_endpoint, schema_name} = DbPortal.DbMetadata.endpoints_and_schema(schema)

    with {:ok, result} <- DbPortal.MonitoredDbRepo.exec_ro_query(q, rd_endpoint, schema_name),
         %MyXQL.Result{rows: rows, columns: cols} <- result do
      # get one row w/many cols and turn to map col->val
      kvs = Enum.zip(cols, hd(rows)) |> Map.new()
      {:ok, kvs}
    else
      {:error, _e} = err -> err
    end
  end

  def provision_db_user(cluster_endpoint, schema_name, user_name, password) do
    db_user = "'#{user_name}'@'%'"

    with_dynamic_repo(cluster_endpoint, schema_name, fn ->
      with {:ok, _} <- query("CREATE USER IF NOT EXISTS #{db_user} IDENTIFIED BY '#{password}'"),
           # in case user already existed, set password separately
           {:ok, _} <- query("ALTER USER #{db_user} IDENTIFIED BY '#{password}'"),
           # choose to give perms on all schemas.  in case of fire...
           {:ok, _} <-
             query(
               "GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, PROCESS, SHOW DATABASES, SHOW VIEW, EXECUTE on *.* to #{db_user}"
             ) do
        :ok
      else
        {:error, %MyXQL.Error{message: e}} -> {:error, e}
      end
    end)
  end

  def delete_database_user(db_user_name, cluster_endpoint, schema_name) do
    db_user = "'#{db_user_name}'@'%'"

    with_dynamic_repo(cluster_endpoint, schema_name, fn ->
      case query("DROP USER #{db_user}") do
        {:ok, _} -> :ok
        {:error, %MyXQL.Error{message: e}} -> {:error, e}
      end
    end)
  end
end
