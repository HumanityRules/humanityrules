defmodule DbPortal.Sampler do
  @moduledoc ~S"""
  Performs the workflow of sampling the db
  """

  alias DbPortal.{Digest, QueryText, MonitoredDbRepo, SampleStore}

  defmodule Sample do
    defstruct [:host, :digest, :mysql_digest, :digest_text, :query_example, :tags, :sampled_at ]
  end

  def sample(host) do
    {:ok, rows} = MonitoredDbRepo.sample(host)

    rows
    |> to_samples()
    |> SampleStore.record_samples()
  end

  defp to_samples(rows) do
    rows
    |> Enum.map(&to_sample/1)
  end

  defp to_sample([host, mysql_digest, query_text, digest_text, sampled_at]=_sample) do
    %Sample{
      host: host,
      digest: Digest.hash(digest_text),
      mysql_digest: mysql_digest,
      digest_text: digest_text,
      query_example: query_text,
      tags: [ ["host", host] | QueryText.tags(query_text)],
      sampled_at: sampled_at
    }
  end

end
