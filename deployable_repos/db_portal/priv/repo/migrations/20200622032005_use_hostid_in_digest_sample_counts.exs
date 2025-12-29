defmodule DbPortal.Repo.Migrations.UseHostidInDigestSampleCounts do
  use Ecto.Migration

  require Logger

  def change do

    alter table(:digest_sample_counts) do
      add :host_id, :integer
    end

    execute ~s"""
      UPDATE digest_sample_counts dsc
      JOIN vc_hosts h on h.name=dsc.host
      SET dsc.host_id = h.id
    """

    execute ~s"""
      ALTER TABLE digest_sample_counts
        DROP PRIMARY KEY
    """

    alter table(:digest_sample_counts) do
      remove :host
      modify :hour, :utc_datetime, primary_key: true
      modify :host_id, :integer, null: false, primary_key: true
      modify :digest, :string, size: 16, primary_key: true
      modify :tag_id, :bigint, primary_key: true
    end
  end
end
