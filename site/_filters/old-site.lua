-- {{< old-site >}}: link to the old site if `old_site_url` is set in
-- _variables.yml, otherwise nothing. Deleting that key (task 6.3) removes
-- every old-site link that uses this shortcode.
-- {{< old-site text="..." >}} overrides the link text.
local function read_var()
  local f = io.open(quarto.project.directory .. "/_variables.yml", "r")
  if not f then return nil end
  local text = f:read("*a")
  f:close()
  local url = text:match("\nold_site_url:%s*([^%s#]+)") or text:match("^old_site_url:%s*([^%s#]+)")
  if url == nil or url == '""' or url == "''" then return nil end
  return url
end

return {
  ["old-site"] = function(args, kwargs)
    local url = read_var()
    if url == nil then return pandoc.Inlines({}) end
    local text = pandoc.utils.stringify(kwargs["text"] or "")
    if text == "" then text = "Old site (" .. url:gsub("^https?://", "") .. ")" end
    return pandoc.Link(text, url)
  end
}
