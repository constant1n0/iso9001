-- 1. Crear la base de datos
CREATE DATABASE GestionCalidadISO9001;
\c GestionCalidadISO9001;

-- 2. Crear las tablas

-- Tabla: Partes_Interesadas
DROP TABLE IF EXISTS Partes_Interesadas;
CREATE TABLE Partes_Interesadas (
    id_interesado SERIAL PRIMARY KEY,
    nombre VARCHAR(50) NOT NULL UNIQUE,
    necesidades_expectativas TEXT,
    requisitos_identificados TEXT,
    objetivo_estrategico TEXT
);

-- Tabla: Roles_Responsabilidades
DROP TABLE IF EXISTS Roles_Responsabilidades;
CREATE TABLE Roles_Responsabilidades (
    id_rol SERIAL PRIMARY KEY,
    rol VARCHAR(50) NOT NULL UNIQUE,
    compromiso_calidad BOOLEAN DEFAULT FALSE,
    descripcion_politica_calidad TEXT
);

-- Tabla: Riesgos_Oportunidades
DROP TABLE IF EXISTS Riesgos_Oportunidades;
CREATE TABLE Riesgos_Oportunidades (
    id_riesgo SERIAL PRIMARY KEY,
    tipo VARCHAR(20) NOT NULL CHECK (tipo IN ('Riesgo', 'Oportunidad')),
    descripcion TEXT NOT NULL,
    objetivo_calidad TEXT,
    plan_accion TEXT
);

-- Tabla: Recursos_Capacitacion
DROP TABLE IF EXISTS Recursos_Capacitacion;
CREATE TABLE Recursos_Capacitacion (
    id_recurso SERIAL PRIMARY KEY,
    recurso_necesario TEXT NOT NULL,
    capacitacion_personal BOOLEAN DEFAULT FALSE,
    descripcion_documentacion TEXT
);

-- Tabla: Procesos_Operacion
DROP TABLE IF EXISTS Procesos_Operacion;
CREATE TABLE Procesos_Operacion (
    id_proceso SERIAL PRIMARY KEY,
    proceso VARCHAR(100) NOT NULL UNIQUE,
    criterio_calidad TEXT,
    control_proveedor BOOLEAN DEFAULT FALSE,
    no_conformidad TEXT
);

-- Tabla: Auditorias_Indicadores
DROP TABLE IF EXISTS Auditorias_Indicadores;
CREATE TABLE Auditorias_Indicadores (
    id_auditoria SERIAL PRIMARY KEY,
    area_auditoria VARCHAR(50) NOT NULL,
    fecha_auditoria DATE DEFAULT CURRENT_DATE,
    resultado TEXT,
    accion_correctiva TEXT,
    indicador_desempeno TEXT
);

-- Tabla: Mejoras
DROP TABLE IF EXISTS Mejoras;
CREATE TABLE Mejoras (
    id_mejora SERIAL PRIMARY KEY,
    no_conformidad TEXT NOT NULL,
    accion_correctiva TEXT,
    accion_preventiva TEXT,
    fecha_implementacion DATE DEFAULT CURRENT_DATE
);

-- Tabla: Users (nueva tabla para autenticación)
DROP TABLE IF EXISTS Users;
CREATE TABLE Users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(150) NOT NULL UNIQUE,
    password VARCHAR(200) NOT NULL
);
