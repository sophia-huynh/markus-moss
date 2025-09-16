function CodeBlock (el)
  -- Apply the 'highlight' style if highlight is listed as a class
  for _, class in ipairs(el.classes) do
    if class == "highlight" then
      if FORMAT:match 'latex' then
        el.attr.attributes['style'] = "highlight"
      end
    end 
  end

  return el
end
