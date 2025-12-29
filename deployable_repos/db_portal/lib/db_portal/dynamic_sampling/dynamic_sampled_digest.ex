defmodule DbPortal.DynamicSampling.DynamicSampledDigest do

  use Ecto.Schema
#  alias __MODULE__

  @primary_key false
  schema "dynamic_sampled_digests" do
    field :digest, :string, primary_key: true
    field :mysql_digest, :string
    field :host, :string
    field :digest_text, :string
    field :query_example, :string
    field :first_seen_at, :utc_datetime
  end
end
