defmodule DbPortal.Repo.Migrations.CreateDigestSampleCounts do
  use Ecto.Migration

  def change do
    create table(:digest_sample_counts, primary_key: false) do
      add :hour, :utc_datetime, primary_key: true
      add :host, :string, size: 256, primary_key: true
      add :digest, :string, size: 16, primary_key: true
      add :tag_id, :bigint, primary_key: true
      add :count, :integer
    end
  end
end
