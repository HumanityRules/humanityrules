defmodule DbPortal.Repo.Migrations.CreateTagCountsPerHour do
  use Ecto.Migration

  def up do

    
    create table(:tag_counts_per_hour, primary_key: false) do
      add :digest, :string, size: 16, null: false, primary_key: true
      add :hour, :utc_datetime, null: false, primary_key: true
      add :key, :string, size: 32, null: false, primary_key: true
      add :total, :integer, null: false
    end

    # now backfill
    execute ~s"""
      INSERT INTO tag_counts_per_hour(digest, hour, `key`, total)
      SELECT digest, hour, t.key, SUM(dsc.count) total
      FROM digest_sample_counts dsc
      JOIN tags t ON t.id = tag_id
      GROUP BY digest, hour, t.key
    """
  end

  def down do
    drop table(:tag_counts_per_hour)
  end
end
