my_function () {
    echo converting $notebook
    jupyter nbconvert --clear-output --inplace "$notebook"
    jupyter nbconvert --execute --to notebook --inplace "$notebook"
    jupyter nbconvert --ClearMetadataPreprocessor.enabled=True --inplace "$notebook"
}
nb_dir="docs/source/notebooks"
for entry in `ls $nb_dir | grep \.ipynb`; do
    notebook=$nb_dir/$entry
    if [[ $notebook =~ ".ipynb" ]]
    then
      my_function
    fi
done
