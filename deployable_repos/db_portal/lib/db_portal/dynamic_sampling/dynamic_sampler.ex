defmodule DbPortal.DynamicSampling.DynamicSampler do
  @moduledoc """
  The DynamicSampler can be started and stopped, which is what the Dynamic
  refers to.  While running, it will sample running queries on a given
  Aurora/Mysql cluster, checking the reader and the writer endpoints. (Note:
  if there are multiple readers, only one will be checked each sampling
  interval, as determined by the Aurora R/O endpoint.)

  It will cache the query info in its own GenServer state and write to
  the database whenever the number of cached queries exceeds a predefined
  limit.

  The caching will enforce uniqueness, but only within the cache itself.
  For queries already written to the DB, we'll just rely on DB uniqueness
  to enforce that (ignore duplicates).
  """
  use GenServer

  require Logger
  alias DbPortal.{Digest, MonitoredDbRepo, Repo}

  @sample_interval_seconds 60
  @sample_interval_millis 1000 * @sample_interval_seconds
  @max_cache_seconds 600

  @cache_size_threshold 10

  def start_link(cluster_name) do
    GenServer.start_link(__MODULE__, cluster_name, name: {:via, Registry, {Registry.DynamicSampler, cluster_name}} )
  end

  @impl true
  def init(cluster_name) do
    Logger.debug("Starting DynamicSampler for #{cluster_name} in PID #{inspect self()}")
    cluster = Repo.cluster(cluster_name)
    schedule_sampling()
    {:ok, %{
      name: cluster_name,
      write_endpoint: cluster.write_endpoint,
      read_endpoint: cluster.read_endpoint,
      last_write_utc: DateTime.utc_now(),
      samples: []}}
  end

  @impl true
  def handle_info(:sample, state) do
    Logger.debug("Sampling #{state.name} at #{NaiveDateTime.utc_now}")
    new_samples = collect_samples(state.write_endpoint, state.read_endpoint, state.samples)
    {new_samples, last_write_utc} = if should_write_to_db?(new_samples, state.last_write_utc) do
      write_samples_to_db(new_samples)
      {[], DateTime.utc_now()}
    else
      {new_samples, state.last_write_utc}
    end

    schedule_sampling()
    {:noreply, %{state | samples: new_samples, last_write_utc: last_write_utc}}
  end

  defp should_write_to_db?(samples, last_write_utc) do
    elapsed_secs_since_last_write = DateTime.diff(DateTime.utc_now(), last_write_utc, :second)
    num_samples = length(samples)
    (num_samples > @cache_size_threshold) || (num_samples > 0 && elapsed_secs_since_last_write > @max_cache_seconds)
  end

  defp collect_samples(write_endpoint, read_endpoint, existing_samples) do
    new_samples = get_new_samples(write_endpoint) ++ get_new_samples(read_endpoint) ++ existing_samples
    new_samples |> Enum.uniq_by(& &1.digest)
  end

  defp get_new_samples(endpoint) do
    case MonitoredDbRepo.sample(endpoint) do
      {:ok, rows} -> rows |> to_samples()

      {:error, e} ->
        Logger.error("Failed to collect dynamic samples from #{endpoint}: #{inspect e}")
        []
    end
  end

  defp to_samples(rows) do
    rows
    |> Enum.map(&to_sample/1)
  end

  defp to_sample([host, mysql_digest, query_text, digest_text, sampled_at]=_sample) do
    {:ok, first_seen_at} = Ecto.Type.cast(:utc_datetime, sampled_at)
    %{
      digest: Digest.hash(digest_text),
      mysql_digest: mysql_digest,
      host: host,
      digest_text: digest_text,
      query_example: query_text,
      first_seen_at: first_seen_at
    }
  end

  defp write_samples_to_db(samples) do
    samples
    |> Repo.insert_dynamic_sampled_digests()
  end

  defp schedule_sampling() do
    Process.send_after(self(), :sample, @sample_interval_millis)
  end

end
