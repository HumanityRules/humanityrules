defmodule DbPortal.VividCortex.Host do

  @hosts [
    {"write.coursehero.mysql.prod.coursehero.io", 1},
    {"read.coursehero.mysql.prod.coursehero.io", 2},
  ]
  @host_map @hosts |> Map.new

  def hosts(), do: @hosts

  def hostname_to_id(name), do: Map.get(@host_map, name)

  def host_to_id(43) do
    1
    #Map.get(@host_map, host)
  end
  def host_to_id(_) do
    2
    #Map.get(@host_map, host)
  end

end
