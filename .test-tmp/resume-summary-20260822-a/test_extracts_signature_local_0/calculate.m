function [total, count] = calculate(values, scale)
% ignoredCall(values)
text = 'alsoIgnored(values)';
scaled = helper(values) * scale;
total = sum(scaled);
count = numel(values);
end

function output = helper(values)
output = values;
end
