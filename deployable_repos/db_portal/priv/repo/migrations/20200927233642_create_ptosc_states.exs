defmodule DbPortal.Repo.Migrations.CreatePtoscStates do
  use Ecto.Migration

  def change do
    create table(:ptosc_states) do
      add :ptosc_job_id, references(:ptosc_jobs), null: false
      add :status, :string, size: 30, null: false
      add :extra, :string, size: 256
      add :status_at, :naive_datetime, null: false
    end

    create index("ptosc_states", [:ptosc_job_id, :status_at])
  end
end
