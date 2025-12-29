defmodule DbPortal.Repo do
  use Ecto.Repo,
    otp_app: :db_portal,
    adapter: Ecto.Adapters.MyXQL

  require Ecto.Query
  alias __MODULE__
  alias DbPortal.Digest
  alias DbPortal.VividCortex.Host

  defmodule Tag do
    use Ecto.Schema

    schema "tags" do
      field :key, :string
      field :value, :string
    end

    def from_list([k, v]) do
      %__MODULE__{key: k, value: String.slice(v, 0, 256)}
    end
  end

  def clusters(env, permitted_schema_ids \\ :all) do
    q = Ecto.Query.from c in DbPortal.DbMetadata.Cluster,
      where: c.env == ^env,
      where: c.disabled == false,
      preload: [:db_schemas],
      order_by: [c.name]

    q = if permitted_schema_ids != :all do
      Ecto.Query.where(q, [s], s.id in ^permitted_schema_ids)
    else
      q
    end

    all(q)
  end

  def cluster(name) do
    q = Ecto.Query.from c in DbPortal.DbMetadata.Cluster,
      where: c.name == ^name

    one(q)
  end

  def schemas(env, permitted_schema_ids \\ :all) do
    q = Ecto.Query.from s in DbPortal.DbMetadata.DbSchema,
      join: c in DbPortal.DbMetadata.Cluster,
        on: c.id == s.cluster_id and c.disabled == false,
      where: c.env == ^env,
      preload: [:cluster],
      order_by: [s.name]

    q = if permitted_schema_ids != :all do
      Ecto.Query.where(q, [s], s.id in ^permitted_schema_ids)
    else
      q
    end

    all(q)
  end

  def insert_metrics(metrics) do
    db_values = metrics
                |> Enum.map(fn { {hour, digest, host, tag_id}, count} ->
                  [ hour: hour, digest: digest, host_id: Host.hostname_to_id(host), tag_id: tag_id, count: count]
                end)
    insert_all( "digest_sample_counts", db_values)
  end

  def insert_digests(digests) do
    insert_all(Digest, digests)
  end

  def insert_dynamic_sampled_digests(digests) do
    insert_all(DbPortal.DynamicSampling.DynamicSampledDigest, digests, on_conflict: :nothing)
  end

  @doc ~S"""

  ## Example usage
    iex> DbPortal.Repo.insert_tags([ ["foo", "bar"] ])
    [
      ok: %DbPortal.Repo.Tag{
        __meta__: #Ecto.Schema.Metadata<:loaded, "tags">,
        id: 1,
        key: "foo",
        value: "bar"
      }
    ]
  """
  def insert_tags(tags) do
    tags
    |> Enum.map(&Tag.from_list/1)
    |> Enum.map(&insert/1)
  end

  def all_digests do
    q = Ecto.Query.from d in Digest,
      select: d.digest
    all(q)
  end

  def all_tags do
    # This is not all tags!  But, loading all tags in production was timing out (I think) on startup, with over 1M tags to load.
    # and, since we're not using sampling anymore, seemed reasonable to just hack this to succeed by imposing a limit
    query = Ecto.Query.from t in Tag,
        limit: 100000,
        order_by: [desc: t.id]

    all(query)
  end

  def hosts_in_hour(hour) do
    %MyXQL.Result{rows: rows} = Ecto.Adapters.SQL.query!(
      Repo, ~s"""
      SELECT DISTINCT t.value
      FROM digest_sample_counts dsc
      JOIN tags t ON t.id = dsc.tag_id
      WHERE t.key='host'
      AND hour = '#{sql_dt_string(hour)}'
      """
    )
    rows |> Enum.map(&hd/1)
  end

  def digests_in_hour(hour) do
    %MyXQL.Result{rows: rows} = Ecto.Adapters.SQL.query!(
      Repo, ~s"""
      SELECT DISTINCT dsc.digest
      FROM digest_sample_counts dsc
      WHERE hour = '#{sql_dt_string(hour)}'
      """
    )
    rows |> Enum.map(&hd/1)
  end

  @doc """
SELECT digest, hour, t.key, SUM(dsc.count) total
FROM digest_sample_counts dsc
JOIN tags t ON t.id = tag_id
GROUP BY digest, hour, t.key
"""
  def update_counts_per_tag_key(hour) do
    %MyXQL.Result{rows: _rows} = Ecto.Adapters.SQL.query!(
      Repo, ~s"""
      INSERT INTO tag_counts_per_hour(digest, hour, `key`, total)
      SELECT digest, hour, t.key, SUM(dsc.count) total
      FROM digest_sample_counts dsc
      JOIN tags t ON t.id = tag_id
      WHERE hour = '#{sql_dt_string(hour)}'
      GROUP BY digest, hour, t.key
      """
    )
  end

  def analytics_query(sql) do
    %MyXQL.Result{rows: rows} = Ecto.Adapters.SQL.query!(
      Repo, sql)
    rows
  end

  def insert_digest_stats(stats) do
    insert_all "vc_query_stats_by_hour", stats
  end

  defp sql_dt_string(%NaiveDateTime{}=d) do
    "#{%NaiveDateTime{d | microsecond: {0,0}}}"
  end

end
