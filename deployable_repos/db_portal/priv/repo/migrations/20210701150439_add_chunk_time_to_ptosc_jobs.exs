defmodule DbPortal.Repo.Migrations.AddChunkTimeToPtoscJobs do
  use Ecto.Migration

  def change do
    alter table(:ptosc_jobs) do
      add :chunk_time, :decimal, precision: 5, scale: 3
    end
  end
end
