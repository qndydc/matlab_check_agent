function output = normalize(values)
%NORMALIZE Scale values by their maximum.
output = values ./ max(values);
end

