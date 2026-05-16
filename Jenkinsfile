pipeline {
    agent any

    environment {
        IMAGE_NAME     = 'noteflow-app'
        IMAGE_TAG      = "${BUILD_NUMBER}"
        DOCKERHUB_USER = 'tanmaydixit09'
    }

    stages {

        stage('Checkout') {
            steps {
                echo 'Checking out code from GitHub...'
                checkout scm
            }
        }

        stage('Install Dependencies') {
            steps {
                echo 'Installing Python dependencies...'

                bat '''
                    py -m pip install --upgrade pip

                    py -m pip install -r requirements.txt

                    py -m pip install pytest
                '''
            }
        }

        stage('Initialize Database') {
            steps {
                echo 'Initializing SQLite database...'

                bat '''
                    if exist init_db.py (
                        py init_db.py
                    ) else (
                        echo init_db.py not found!
                        exit /b 1
                    )
                '''
            }
        }

        stage('Run Tests') {
            steps {
                echo 'Running unit tests...'

                bat '''
                    py -m pytest test_app.py -v --tb=short
                '''
            }
        }

        stage('Build Docker Image') {
            steps {
                echo 'Building Docker image...'

                bat '''
                    docker build -t %IMAGE_NAME%:%IMAGE_TAG% .

                    docker tag %IMAGE_NAME%:%IMAGE_TAG% %IMAGE_NAME%:latest
                '''
            }
        }

        stage('Push to Docker Hub') {
            steps {
                echo 'Pushing image to Docker Hub...'

                withCredentials([usernamePassword(
                    credentialsId: 'dockerhub-credentials',
                    usernameVariable: 'DOCKER_USER',
                    passwordVariable: 'DOCKER_PASS'
                )]) {

                    bat '''
                        echo %DOCKER_PASS% | docker login -u %DOCKER_USER% --password-stdin

                        docker tag %IMAGE_NAME%:latest %DOCKERHUB_USER%/%IMAGE_NAME%:latest

                        docker tag %IMAGE_NAME%:latest %DOCKERHUB_USER%/%IMAGE_NAME%:%IMAGE_TAG%

                        docker push %DOCKERHUB_USER%/%IMAGE_NAME%:latest

                        docker push %DOCKERHUB_USER%/%IMAGE_NAME%:%IMAGE_TAG%
                    '''
                }
            }
        }

        stage('Deploy to Kubernetes') {
            steps {
                echo 'Deploying to Kubernetes...'

                bat '''
                    kubectl apply -f k8s\\deployment.yaml

                    kubectl apply -f k8s\\service.yaml
                '''
            }
        }
    }

    post {

        success {
            echo 'Pipeline completed successfully!'
        }

        failure {
            echo 'Pipeline failed. Check logs for details.'
        }

        always {
            echo 'Cleaning up Docker images...'

            bat '''
                docker rmi -f %IMAGE_NAME%:%IMAGE_TAG% || ver > nul

                docker rmi -f %IMAGE_NAME%:latest || ver > nul
            '''
        }
    }
}
