TARGET = Specs.pdf
DIAGRAMS = Transfer.eps Model.eps FileDB.eps ArchOverview.eps MessagingClasses.eps Threading.eps UI-prototype.png

all: $(TARGET)

diagrams: $(DIAGRAMS)

%.pdf: %.tex
	pdflatex -halt-on-error $<

%.tex: %.lyx $(DIAGRAMS)
	lyx -e pdflatex $<

%.eps: %.dia
	dia -t eps -e $@ $<

UI-prototype.png: UI-prototype.svg
	inkscape --export-png=$@ --export-dpi=150 $<

clean:
	rm -f *.log *.aux $(TARGET) *.eps *.tex
