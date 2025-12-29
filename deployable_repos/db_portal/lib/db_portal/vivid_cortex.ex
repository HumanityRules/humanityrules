defmodule DbPortal.VividCortex do
  @moduledoc ~S"""
  Responsible for the interactions with VividCortex
    * For each digest sampled in the hour
      - fetch VC stats for that digest,hour
    * For each host sampled in the hour
      - fetch VC stats for the host (not by query digest)
  """

  alias DbPortal.Repo
  alias DbPortal.VividCortex.Client
  require Logger

  def process_hour(hour) do
    try do
      Logger.info "VIVID_CORTEX collection"
      process_host_stats(hour)
      process_digest_stats(hour)
    rescue
      e in Jason.DecodeError -> e
    end
  end

  def process_host_stats(hour) do
    hosts = Repo.hosts_in_hour(hour)
    try do
      Client.stats_for_hosts(hosts, hour)
    rescue
      e in Jason.DecodeError -> e
    end
  end

  def process_digest_stats(hour) do
    Logger.info "VIVID_CORTEX collection"
    try do
      Repo.digests_in_hour(hour)
      |> Client.stats_for_digests(hour)
      |> Repo.insert_digest_stats
      |> IO.inspect(label: "results of process_digest_stats")
    rescue
      e in Jason.DecodeError -> e
    end
  end
end
