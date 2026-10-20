package main

import (
	"fmt"
	"log"
	"net/http"
	"os"

	"github.com/google/uuid"
)

// version is the line the harness changes between releases.
const version = "go v1"

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	host := os.Getenv("HOST")
	if host == "" {
		host = "127.0.0.1"
	}
	boot := uuid.NewString()
	http.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprintf(w, "%s boot=%s\n", version, boot[:8])
	})
	log.Printf("listening on %s:%s", host, port)
	log.Fatal(http.ListenAndServe(host+":"+port, nil))
}
