defmodule DbPortalWeb.DropDownMenuComponent do
  use Phoenix.Component
  import Phoenix.HTML
  import Phoenix.HTML.Form
  use PhoenixHTMLHelpers

  def dropdown(assigns) do
    ~H"""
    <div class="relative inline-block text-left" x-data="dropdown()">
      <div>
        <button type="button" class="inline-flex justify-center w-full rounded-md border border-gray-300 shadow-sm px-4 py-2 bg-white text-sm font-medium text-gray-700 hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-gray-100 focus:ring-indigo-500"
    id="options-menu" aria-haspopup="true" aria-expanded="true" @click="open()">
        <%= @legend %>
        <!-- Heroicon name: chevron-down -->
        <svg class="-mr-1 ml-2 h-5 w-5" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
          <path fill-rule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd" />
        </svg>
        </button>
      </div>

      <div class="origin-top-right absolute left-0 mt-2 w-56 rounded-md shadow-lg bg-white ring-1 ring-black ring-opacity-5 divide-y divide-gray-100"
        role="menu" aria-orientation="vertical" aria-labelledby="options-menu" x-show="isOpen()" @click="close()" @click.away="close()">
        <%= for entry <- @link do %>
          <.ddlink href="#"
            class="block px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 hover:text-gray-900"
            role="menuitem"
            {entry}>

            <%= render_slot(entry) %>
          </.ddlink>
        <% end %>
      </div>
    </div>
    """
  end

  def ddlink(%{divider: _} = assigns) do
    ~H"""
      <div class="py-1"/>
    """
  end

  def ddlink(%{disabled: true}=assigns) do
    ~H"""
    """
  end
  def ddlink(assigns) do
    assigns = assigns
              |> assign_new(:opts, fn -> assigns_to_attributes(assigns) end)
    ~H"""
      <a {@opts}>
          <%= render_slot(@inner_block) %>
      </a>
    """
  end

end
