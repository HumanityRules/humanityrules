defmodule DbPortal.DynamicSampling.SamplerManager do

  alias DbPortal.DynamicSampling.SamplerSupervisor

  def enabled_clusters() do
    SamplerSupervisor.running_samplers()
  end

  def change_sampling(cluster_name, true = enable?) do
    IO.puts("change_sampling #{cluster_name} #{enable?}")
    case SamplerSupervisor.start_sampler(cluster_name) do
      {:ok, _pid} -> :ok
      :ignore -> {:error, "ignored for some reason"}
      {:error, e} -> {:error, "failed for reason #{inspect e}"}
      e -> # some error tuple
        {:error, "failed for reason #{inspect e}"}
    end
  end
  def change_sampling(cluster_name, false = enable?) do
    IO.puts("change_sampling #{cluster_name} #{enable?}")
    case SamplerSupervisor.stop_sampler(cluster_name) do
      :ok -> :ok
      :ignore -> {:error, "ignored for some reason"}
      {:error, e} -> {:error, "failed for reason #{inspect e}"}
      e -> # some error tuple
        {:error, "failed for reason #{inspect e}"}
    end
  end
end

