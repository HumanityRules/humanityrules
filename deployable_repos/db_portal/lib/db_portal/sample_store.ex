defmodule DbPortal.ConCacheSupervisor do
  use Supervisor
  alias DbPortal.SampleStore

  def start_link([]) do
    Supervisor.start_link(__MODULE__, nil, name: __MODULE__)
  end
  def start_link(args) do
    result = ConCache.start_link args

    Keyword.get(args, :name)
    |> SampleStore.reload_cache

    result
  end

  def init(_init_arg) do
    children = [
      concache_child_spec(:digest_cache),
      concache_child_spec(:tag_cache),
      concache_child_spec(:stat_cache),
      %{id: :avatar_cache, start: {DbPortalWeb.Avatar, :start_link,
        [[name: :avatar_cache, ttl_check_interval: false]]}}
    ]
    Supervisor.init(children,
      [strategy: :one_for_one, name: __MODULE__]
    )
  end

  def concache_child_spec(name) do
    %{id: name, start: {__MODULE__, :start_link,
      [[name: name, ttl_check_interval: false]]}}
  end
end

defmodule DbPortal.SampleStore do
  @moduledoc ~S"""

  Handle storing samples, covering the various aspects: query definitions,
  query samples, tags

  Use ConCache to manage the data in ETS
    * :digest_cache
    * :stats_cache
    * :tag_cache

  On a periodic basis, the :stat_cache must be committed to the DB and
  restarted fresh.

  For the query_defs and tags we must track new ones (not in the DB) in order
  to flush the new ones to the db

  """

  alias DbPortal.{Digest}
  alias DbPortal.Repo

  require Logger

  def reload_cache(:digest_cache) do
    Repo.all_digests
    |> Enum.map( &ConCache.put(:digest_cache, &1, true))
  end

  def reload_cache(:tag_cache) do
    Repo.all_tags
    |> Enum.map( &ConCache.put(:tag_cache, [&1.key, &1.value], &1.id))
  end
  def reload_cache(:stat_cache), do: nil

  def record_samples(samples) do
    samples |> find_new_queries() |> save_new_queries()

    samples |> find_new_tags() |> save_new_tags()

    samples |> update_sample_metrics()
  end

  def find_new_queries(samples) do
    samples
    |> Enum.filter(fn %{digest: digest} -> nil==ConCache.get(:digest_cache, digest) end)
  end

  def save_new_queries(new_samples) do
    new_samples
    |> Enum.uniq_by(fn sample-> sample.digest end)
    |> Enum.map(&Digest.from_sample/1)
    |> Repo.insert_digests

    for %{digest: digest} <- new_samples, do: ConCache.put(:digest_cache, digest, true)
  end

  def find_new_tags(samples) do
    samples
    |> Enum.reduce([], fn %{tags: tags},acc -> tags++acc end)
    |> Enum.uniq
    |> Enum.filter(fn tag -> nil==ConCache.get(:tag_cache, tag) end)
  end

  def save_new_tags(tags) do
    r = Repo.insert_tags tags
    # TODO: check for failures???
    for {:ok, tag} <- r do
      ConCache.put(:tag_cache, [tag.key, tag.value], tag.id)
    end
  end

  def update_sample_metrics(samples) do
    samples
    |> Enum.map(&update_sample_metric/1)
  end

  def update_sample_metric(%{digest: digest, host: host, tags: tags, sampled_at: sampled_at}) do
    hour = beginning_of_hour(sampled_at)
    for tag <- tags do
      tag_id = get_tag_id(tag)
      ConCache.update(:stat_cache, {hour, digest, host, tag_id}, fn
        nil -> {:ok, 1}
        count -> {:ok, count+1}
      end)
    end
  end

  @doc ~S"""
    This is expected to be called after the hour is complete, so that
    the data in ConCache for that hour is no longer being mutated.
    Then it will be fine to capture the data and remove it from the
    cache non-transactionally
  """
  def process_hourly_metrics(hour) do
    flush_cached_metrics_to_db(hour)
    update_total_counts_per_tag_key(hour)
  end

  def flush_cached_metrics_to_db(hour) do
    table = ConCache.ets(:stat_cache)
    metrics = :ets.match_object(table, {{hour, :_, :_, :_}, :_})
    Logger.info "process_hourly_metrics: #{length(metrics)} hour #{inspect hour}"
    DbPortal.Repo.insert_metrics(metrics)
    for m<-metrics, do: ConCache.delete(:stat_cache, elem(m,0))
    metrics
  end

  def update_total_counts_per_tag_key(hour) do
    DbPortal.Repo.update_counts_per_tag_key(hour)
  end

  def get_tag_id(tag) do
    ConCache.get(:tag_cache, tag)
  end

  def beginning_of_hour(%NaiveDateTime{} = d) do
    %NaiveDateTime{ d | minute: 0, second: 0, microsecond: {0,0} }
  end
end
