defmodule DbPortal.Secrets do
  use Agent

  def start_link(_opts) do
    Agent.start_link(fn -> nil end, name: Secrets)
  end

  def init(pw) do
    Agent.cast(Secrets, fn _x -> pw end)
  end

  def value() do
    Agent.get(Secrets, &(&1))
  end

end
