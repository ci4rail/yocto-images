#/bin/bash

if [ "$#" -ne 3 ] ; then
    echo "Usage: ${0} <template_file> <data_file> <output_file>"
    exit 1
fi

template_file=$1
data_file=$2
output_file=$3

cp ${template_file} ${output_file}

while IFS= read -r line; do
    var=$(echo $line | cut -d ":" -f 1)
    sub=$(echo $line | cut -d ":" -f 2)
    sub=$(echo $sub | xargs)
    echo "var=$var sub=$sub"
    sed -i -e "s/___${var}___/${sub}/g" ${output_file}
done < "$data_file"
