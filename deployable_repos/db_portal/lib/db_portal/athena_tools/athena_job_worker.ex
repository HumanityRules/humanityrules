defmodule DbPortal.AthenaTools.AthenaJobWorker do
  use GenServer

  require Logger
  alias DbPortal.AthenaTools.{AthenaAws, AthenaJobServer}
  alias DbPortal.DbMetadata.Aws


  def start_link(_default_state) do
    GenServer.start_link(__MODULE__ , %{}, name: __MODULE__)
  end

  def init(state) do
    {:ok, state}
  end

  def handle_cast({:new_athena_job, athena_job}, state) do
    GenServer.call(AthenaJobServer, {:athena_job_worker_start, athena_job})

    state = state |> Map.put(:athena_job, athena_job)

    try do
      case athena_job do
        %{:job_type => "MoveToRecycleBin"} ->
          move_to_recycle_bin(athena_job)

        %{:job_type => "CopyTables"} ->
          copy_or_move_tables!(athena_job, &AthenaAws.copy_table!/4)

        %{:job_type => "MoveTables"} ->
          copy_or_move_tables!(athena_job, &AthenaAws.move_table!/4)

        %{:job_type => "DeletePermanently"} ->
          delete_permanently(athena_job)

        %{:job_type => unknown} ->
          log_to_server(:error, "AthenaJobWorker error: Unknown job type #{unknown}")
      end
    rescue
      exception in RuntimeError ->
        log_to_server(:error, exception.message)
        log_to_server(:error, inspect(__STACKTRACE__))
    end

    GenServer.call(AthenaJobServer, {:athena_job_worker_end, athena_job})

    {:noreply, state}
  end


  defp copy_or_move_tables!(athena_job, athena_aws_func) do
    if length(athena_job.selected_tables) > 1
        and not String.match?(athena_job.dst_table.location, ~r/{table_name}/)
        and not String.match?(athena_job.dst_table.table_name, ~r/{table_name}/) do
      raise "Table name and Location must contain a pattern for substitution when more than 1 table is selected"
    end

    Enum.each(athena_job.selected_tables,
      fn src_table ->
        log_to_server(:info, "-------------------------- Table #{src_table.table_name} --------------------------")
        dst_table_name = String.replace(athena_job.dst_table.table_name, ~r/{table_name}/, src_table.table_name)
        dst_location = String.replace(athena_job.dst_table.location, ~r/{table_name}/, src_table.table_name)
        dst_table = %{
          :database_name => athena_job.dst_table.database_name,
          :table_name => dst_table_name,
          :location => dst_location
        }
        try do
          athena_aws_func.(src_table, dst_table, Aws.aws_client(), &log_to_server/2)
        rescue
          exception in RuntimeError ->
            log_to_server(:error, exception.message)
            log_to_server(:error, inspect(__STACKTRACE__))
        end
      end
      )

  end


  defp move_to_recycle_bin(athena_job) do
    Enum.each(athena_job.src_tables,
      fn table_name ->
        try do
          log_to_server(:info, "-------------------------- Table #{table_name} --------------------------")
          AthenaAws.move_to_recycle_bin!(%{:database_name => athena_job.src_database, :table_name => table_name},
                                        Aws.aws_client(), &log_to_server/2)
        rescue
          exception in RuntimeError ->
            log_to_server(:error, exception.message)
            log_to_server(:error, inspect(__STACKTRACE__))
        end
      end)
  end


  defp delete_permanently(athena_job) do
    Enum.each(athena_job.src_tables,
      fn table_name ->
        try do
          log_to_server(:info, "-------------------------- Table #{table_name} --------------------------")
          AthenaAws.delete_table_permanently!(%{:database_name => athena_job.src_database, :table_name => table_name},
                                              Aws.aws_client(), &log_to_server/2)
        rescue
          exception in RuntimeError ->
            log_to_server(:error, exception.message)
            log_to_server(:error, inspect(__STACKTRACE__))
        end
      end)
  end


  defp log_to_server(level, log_msg) do
    GenServer.call(AthenaJobServer, {:athena_job_worker_new_log_message, level, log_msg})
  end

end
