defmodule DbPortal.DynamicSampling.SamplerSupervisor do
  use DynamicSupervisor

  require Logger
  alias DbPortal.DynamicSampling.DynamicSampler

  def start_link(_init_args \\ []) do
    DynamicSupervisor.start_link(__MODULE__, :ok, name: __MODULE__)
  end

  def init(:ok) do
    Logger.debug("Starting #{__MODULE__} in PID #{inspect self()}")
    DynamicSupervisor.init(strategy: :one_for_one)
  end

  def start_sampler(cluster_name) do
    DynamicSupervisor.start_child(__MODULE__, {DynamicSampler, cluster_name})
  end

  def stop_sampler(cluster_name) do
    Registry.select(Registry.DynamicSampler, [{{:"$1", :"$2", :"$3"}, [{:== , :"$1", cluster_name}], [:"$2"]}])
    |> List.first()
    |> then(& DynamicSupervisor.terminate_child(__MODULE__, &1) )
  end

  def running_samplers() do
    Registry.select(Registry.DynamicSampler, [{{:"$1", :"$2", :"$3"}, [], [:"$1"]}])
    |> MapSet.new()
  end
end
