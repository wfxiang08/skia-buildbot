package main

import (
	"database/sql"
	"flag"
	"fmt"
	"html/template"
	"io/ioutil"
	"log"
	"math/rand"
	"net/http"
	"os"
	"path/filepath"
	"time"
)

import (
	"github.com/fiorix/go-web/autogzip"
	_ "github.com/go-sql-driver/mysql"
	_ "github.com/mattn/go-sqlite3"
)

var (
	// indexTemplate is the main index.html page we serve.
	indexTemplate *template.Template = nil

	// db is the database, nil if we don't have an SQL database to store data into.
	db *sql.DB = nil
)

// flags
var (
	port = flag.String("port", ":8000", "HTTP service address (e.g., ':8000')")
)

func init() {
	rand.Seed(time.Now().UnixNano())

	// Change the current working directory to the directory of the executable.
	var err error
	cwd, err := filepath.Abs(filepath.Dir(os.Args[0]))
	if err != nil {
		log.Fatalln(err)
	}
	if err := os.Chdir(cwd); err != nil {
		log.Fatalln(err)
	}

	indexTemplate = template.Must(template.ParseFiles(filepath.Join(cwd, "templates/index.html")))

	// Connect to MySQL server. First, get the password from the metadata server.
	// See https://developers.google.com/compute/docs/metadata#custom.
	req, err := http.NewRequest("GET", "http://metadata/computeMetadata/v1/instance/attributes/readwrite", nil)
	if err != nil {
		log.Fatalln(err)
	}
	client := http.Client{}
	req.Header.Add("X-Google-Metadata-Request", "True")
	if resp, err := client.Do(req); err == nil {
		password, err := ioutil.ReadAll(resp.Body)
		if err != nil {
			log.Fatalln("ERROR: Failed to read password from metadata server:", err)
		}
		// The IP address of the database is found here:
		//    https://console.developers.google.com/project/31977622648/sql/instances/skiaperf/overview
		// And 3306 is the default port for MySQL.
		db, err = sql.Open("mysql", fmt.Sprintf("readwrite:%s@tcp(173.194.104.24:3306)/skia?parseTime=true", password))
		if err != nil {
			log.Fatalln("ERROR: Failed to open connection to SQL server:", err)
		}
	} else {
		log.Println("INFO: Failed to find metadata, unable to connect to MySQL server (Expected when running locally):", err)
		// Fallback to sqlite for local use.
		db, err = sql.Open("sqlite3", "./perf.db")
		if err != nil {
			log.Fatalln("ERROR: Failed to open:", err)
		}
		// TODO(jcgregorio) Add CREATE TABLE commands here for local testing.
	}

	// Ping the database to keep the connection fresh.
	go func() {
		c := time.Tick(1 * time.Minute)
		for _ = range c {
			if err := db.Ping(); err != nil {
				log.Println("ERROR: Database failed to respond:", err)
			}
		}
	}()
}

// reportError formats an HTTP error response and also logs the detailed error message.
func reportError(w http.ResponseWriter, r *http.Request, err error, message string) {
	log.Println("Error:", message, err)
	w.Header().Set("Content-Type", "text/plain")
	http.Error(w, message, 500)
}

// mainHandler handles the GET and POST of the main page.
func mainHandler(w http.ResponseWriter, r *http.Request) {
	log.Printf("Main Handler: %q\n", r.URL.Path)
	if r.Method == "GET" {
		w.Header().Set("Content-Type", "text/html")
		if err := indexTemplate.Execute(w, struct{}{}); err != nil {
			log.Println("ERROR: Failed to expand template:", err)
		}
	}
}

func main() {
	flag.Parse()
	// Resources are served directly.
	http.Handle("/res/", autogzip.Handle(http.FileServer(http.Dir("./"))))

	http.HandleFunc("/", autogzip.HandleFunc(mainHandler))
	log.Fatal(http.ListenAndServe(*port, nil))
}