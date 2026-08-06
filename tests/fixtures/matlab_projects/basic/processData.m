function output = processData(values)
%PROCESSDATA Normalize and sum values.
normalized = utils.normalize(values);
output = helper(normalized);
end

function total = helper(values)
total = sum(values);
end

