// Ktor with its Gradle plugin: buildFatJar writes one runnable jar with everything inside.
plugins {
    kotlin("jvm") version "2.0.21"
    id("io.ktor.plugin") version "2.3.12"
}

repositories {
    mavenCentral()
}

kotlin {
    jvmToolchain(21)
}

application {
    mainClass.set("app.ApplicationKt")
}

dependencies {
    implementation("io.ktor:ktor-server-core-jvm")
    implementation("io.ktor:ktor-server-netty-jvm")
    implementation("ch.qos.logback:logback-classic:1.5.8")
}
