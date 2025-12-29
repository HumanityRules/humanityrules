defmodule DbPortal.Repo.Migrations.CreateVcQueryStatsByHour do
  use Ecto.Migration

  def change do
    create table(:vc_query_stats_by_hour) do
      add :digest, :string, size: 16, null: false
      add :host_id, :int, null: false
      add :hour, :utc_datetime, null: false
      add :stat_id, :int, null: false
      add :value, :decimal, precision: 13, scale: 4, null: false
    end

  end

end
